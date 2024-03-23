import struct

class MicroFont:
    def __init__(self,filename,cache_index = False):
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
        self.max_width = max_width,
        self.monospaced = True if monospaced else False
        self.index_len = index_len # Sparse index length on disk.
        self.cache_index = cache_index
        self.index = None
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

    def get_ch(self, ch):
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

        # Access the char data inside the file.
        self.stream.seek(12+self.index_len+doff) # 12 is header len.
        width = self.read_int_16(self.stream.read(2))
        char_data_len = (width + 7)//8 * self.height
        char_data = self.stream.read(char_data_len)
        return char_data, self.height, width

if __name__ == "__main__":
    font = MicroFont("victor:B:12.mfnt")
    print(font.get_ch("a"))
