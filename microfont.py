import struct, framebuf, math

# This is a lookup table for fasth computation of sin() and cos() functions
# of degrees from 0 to 360. At postion "A" the table stores sin(A)*64+64,
# (with A in degrees). For values >= 180 we just invert the sign. For
# cos, we just add 90 degrees.
FAST_SIN_TABLE = b'@ABCDEFGHJKLMNOPQRSTUVWYZ[\\]^__`abcdefghiijklmnnopqqrsstuuvvwwxyyzzz{{|||}}}~~~~\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x80\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f\x7f~~~~}}}|||{{zzzyyxwwvvuutssrqqponnmlkjiihgfedcba`__^]\\[ZYWVUTSRQPONMLKJHGFEDCBA'

COLORMODE_MONO_HLSB = const(0)
COLORMODE_RGB_565 = const(1)

def fast_sin(angle):
    angle = int(angle) % 360
    if angle >= 180:
        return -(FAST_SIN_TABLE[angle%180]-64)
    else:
        return FAST_SIN_TABLE[angle]-64

def fast_cos(angle): return fast_sin(angle+90)

class MicroFont:
    def __init__(self,filename,cache_index = False, cache_chars = False):
        stream = open(filename,"rb")
        header_data = stream.read(12)
        if len(header_data) != 12:
            raise ValueError("Corrupted header for MFNT font file")
        magic,height,baseline,max_width,monospaced,index_len = \
            struct.unpack("<4sBBBBL",header_data)
        if magic != b'MFNT':
            raise ValueError(f"{filename} is not a MicroFont file")
        self.height = height
        self.baseline = baseline
        self.max_width = max_width
        self.monospaced = True if monospaced else False
        self.index_len = index_len # Sparse index length on disk.
        self.cache_chars = cache_chars
        self.cache_index = cache_index or cache_chars
        self.index = None
        self.cache = {}
        self.stream = stream # We keep the file open for lower latecy.

    def height(): return self.height
    def baseline(): return self.baseline
    def max_width(): return self.max_width
    def monospaced(): return self.monospaced

    def read_int_16(self,l):
        return l[0] | (l[1] << 8)

    # Binary search of the sparse index.
    def bs(self, index, val):
        while True:
            m = (len(index) & ~ 7) >> 1
            v = index[m] | index[m+1] << 8
            if v == val:
                return index[m+2] | index[m+3] << 8
            if not m:
                return 0
            index = index[m:] if v < val else index[:m]

    # Return the character bitmap (horizontally mapped, and horizontally
    # padded to whole bytes), the height and width in pixels.
    def get_ch(self, ch):
        if self.cache_chars and ch in self.cache:
            return self.cache[ch]

        # Read the index in memory, if not cached.
        if self.index != None:
            index = self.index
        else:
            self.stream.seek(0)
            index = self.stream.read(self.index_len)
            if self.cache_index: self.index = index

        # Get the character data offset inside the file
        # relative to the start of the data section, so the
        # real offset from the start is hdr_len + index_len + doff.
        doff = self.bs(memoryview(index), ord(ch)) << 3

        # Access the char data inside the file and return it.
        self.stream.seek(12+self.index_len+doff) # 12 is header len.
        width = self.read_int_16(self.stream.read(2))
        char_data_len = (width + 7)//8 * self.height
        char_data = self.stream.read(char_data_len)
        retval = char_data, self.height, width
        if self.cache_chars: self.cache[ch] = retval
        return retval

    # Lowlevel framebuffer function. That's the core of the library, as handles
    # the actual drawing of the character to the target framebuffer memory
    # with rotation, oversampling and so forth.
    @micropython.viper
    def draw_ch_blit(self, fb:ptr8, fb_width:int, fb_len:int, ch_buf:ptr8, ch_width:int, ch_height:int, dst_x:int, dst_y:int, off_x:int, off_y:int, color:int, sin_a:int, cos_a:int, colormode:int):
        for y in range(ch_height):
            for x in range(ch_width):
                ch_byte = (ch_width>>3)*y + (x>>3)
                ch_pixel = (ch_buf[ch_byte] >> (7-(x&7))) & 1
                if ch_pixel == 0: continue
                # Floating point math is very slow in common MCUs, so
                # we execute all the computation by multiplying by 64
                # (fixed point numbers) and finally divide by 64*64 to
                # obtain the pixel integer value.
                #
                # Step is used for oversampling, otherwise when the text
                # is rotated there will be empty pixels. We just oversample
                # in the diagonal, since that's enough to fill the gaps.
                for step in range(2):
                    s = (step<<4)+(step<<3)
                    dx = dst_x + (((((x+off_x)<<6)+s)*cos_a - (((y+off_y)<<6)+s)*sin_a + (1<<11))>>12)
                    dy = dst_y + (((((x+off_x)<<6)+s)*sin_a + (((y+off_y)<<6)+s)*cos_a + (1<<11))>>12)
                    if colormode == COLORMODE_MONO_HLSB:
                        fb_byte = (dy*fb_width+dx)>>3
                        if fb_byte < 0 or fb_byte >= fb_len or \
                           dx >= fb_width or dx < 0:
                            continue
                        fb_bit_shift = 7-((dx)&7)
                        fb_bit_mask = 0xff ^ (1<<fb_bit_shift)
                        fb[fb_byte] = (fb[fb_byte] & fb_bit_mask) | \
                                      (color << fb_bit_shift)
                    elif colormode == COLORMODE_RGB_565:
                        fb_word = (dy*fb_width+dx)
                        if fb_word < 0 or fb_word >= (fb_len>>1) or \
                           dx >= fb_width or dx < 0:
                            continue
                        fb16 = ptr16(fb)
                        fb16[fb_word] = color

    # Write a character in the destination MicroPython framebuffer 'fb'
    # setting all the pixels that are set on the font to 'color'.
    # The character 'ch' must be obtained with the get_ch() method.
    # The 'color' must be an integer in the correct format for the specified
    # framebuffer format (fb_fmt). fb_width is the framebuffer width in
    # pixels.
    #
    # The character is printed at dst_x, dst_y (top-left corner of the
    # character), however if off_x is specified, the character is printed
    # at the right of the specified position by off_x pixels. This is
    # different than just adding the same amount of pixels to dst_x, since
    # with rotation we need to move along the rotation direction. This
    # is useful when using draw_ch() to print multiple characters of the
    # same string: we start with off_x, and increment off_x based on the
    # width of the already printed chars. The same applies to off_y, but
    # for vertical offsets due to multi-line text rendering.
    def draw_ch(self, ch, fb, fb_fmt, fb_width, fb_height, dst_x, dst_y, color, off_x=0, off_y=0, rot=0):
        ch_buf = ch[0]
        ch_height = ch[1]
        # Characters horizontal bits are padded with zeros to byte boundary,
        # so let's compute the actual pixels width including padding.
        ch_width = ((ch[2] + 7) // 8) * 8

        # The lower-level drawing functions take the angle as integers
        # representing the sin() and cos() value of the angle multiplyed
        # by 64. This is needed since lower-level functions are implemented
        # using Viper, that only allows to use integer math.
        # We have a fast-path for obvious rotations (it makes a difference).
        if rot == 0:
            sin = 0; cos = 64
        elif rot == 90:
            sin = 64; cos = 0
        elif rot == 180:
            sin = 0; cos = -64
        elif rot == 270:
            sin = -64; cos = 0
        else:
            sin = fast_sin(rot)
            cos = fast_cos(rot)

        # Call the lower level function depending on the target
        # framebuffer color mode.
        if fb_fmt == framebuf.MONO_HLSB:
            fb_len = fb_width*fb_height//8
            self.draw_ch_blit(fb,fb_width,fb_len,ch_buf,ch_width,ch_height,dst_x,dst_y,off_x,off_y,color,sin,cos,COLORMODE_MONO_HLSB)
        elif fb_fmt == framebuf.RGB565:
            fb_len = fb_width*fb_height*2
            self.draw_ch_blit(fb,fb_width,fb_len,ch_buf,ch_width,ch_height,dst_x,dst_y,off_x,off_y,color,sin,cos,COLORMODE_RGB_565)
        else:
            raise ValueError("Unsupported framebuffer color format")

    # Render the text 'txt' in the fb of format fb_fmt of size fb_width,
    # fb_height, starting writing at x,y (top-left corner), with the
    # specified color (given as integer in the format of fb_fmt).
    # By default the text is not rotated.
    #
    # If 'txt' contains newlines, they are handled as expected, starting
    # a new line under the first one and back to the left. This also works
    # correctly with rotations, so you can use this function in order to
    # display rotated multi-line text.
    def write(self, txt, fb, fb_fmt, fb_width, fb_height, x, y, color, *, rot=0, x_spacing=0, y_spacing=0):
        off_x = 0
        off_y = 0
        for c in txt:
            if c == '\n':
                off_y += self.height+y_spacing
                off_x = 0
                continue
            ch = self.get_ch(c)
            self.draw_ch(ch,fb,fb_fmt,fb_width,fb_height,x,y,color,off_x,off_y,rot)
            off_x += x_spacing + ch[2]

if __name__ == "__main__":
    font = MicroFont("victor:B:12.mfnt")
    data,height,width = font.get_ch("Q")
    for i in range(height):
        print(bin(data[i]|1024))
