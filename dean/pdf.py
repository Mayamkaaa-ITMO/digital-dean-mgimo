"""A small PDF writer for text-only demo documents and an embedded TrueType font."""

import re
from functools import lru_cache
from pathlib import Path
from struct import iter_unpack, pack, unpack_from
from zlib import compress, decompressobj, error as ZlibError

FONT_PATH = Path(__file__).with_name("assets") / "NotoSans-Regular.ttf"
DEMO_HEADING = "ДЕМОНСТРАЦИОННЫЙ ДОКУМЕНТ"
DOCUMENT_PAGE = re.compile(
    rb"\n<< /Type /Page /Parent 2 0 R /MediaBox \[0 0 595 842\] "
    rb"/Resources << /Font << /F1 3 0 R >> >> /Contents (\d{1,8}) 0 R >>\nendobj\n"
    rb"\1 0 obj\n<< /Length (\d{1,8}) /Filter /FlateDecode  >>\nstream\n"
)
DOCUMENT_LINE = re.compile(rb"BT /F1 \d+ Tf 1 0 0 1 54 \d+ Tm <([0-9A-F]*)> Tj ET")


class DocumentFont:
    def __init__(self, data):
        self.data = data
        count = unpack_from(">H", data, 4)[0]
        tables = {key: offset for key, _, offset, _ in iter_unpack(">4sIII", data[12:12 + 16 * count])}
        self.units = unpack_from(">H", data, tables[b"head"] + 18)[0]
        self.bbox = tuple(self.scale(v) for v in unpack_from(">hhhh", data, tables[b"head"] + 36))
        self.ascent, self.descent = (self.scale(v) for v in unpack_from(">hh", data, tables[b"hhea"] + 4))
        count = unpack_from(">H", data, tables[b"hhea"] + 34)[0]
        start = tables[b"hmtx"]
        self.advances = tuple(self.scale(width) for width, _ in iter_unpack(">Hh", data[start:start + count * 4]))
        cmap = tables[b"cmap"]
        encodings = unpack_from(">H", data, cmap + 2)[0]
        offsets = [cmap + offset for platform, encoding, offset in
                   iter_unpack(">HHI", data[cmap + 4:cmap + 4 + encodings * 8])
                   if platform == 0 or (platform, encoding) == (3, 1)]
        start = next(offset for offset in offsets if unpack_from(">H", data, offset)[0] == 4)
        segments = unpack_from(">H", data, start + 6)[0] // 2
        end_at = start + 14
        start_at = end_at + 2 * segments + 2
        delta_at = start_at + 2 * segments
        range_at = delta_at + 2 * segments
        self.glyphs = {}
        for i in range(segments):
            end = unpack_from(">H", data, end_at + 2 * i)[0]
            begin = unpack_from(">H", data, start_at + 2 * i)[0]
            delta = unpack_from(">h", data, delta_at + 2 * i)[0]
            address = range_at + 2 * i
            displacement = unpack_from(">H", data, address)[0]
            for code in range(begin, min(end, 0xFFFE) + 1):
                glyph = code
                if displacement:
                    glyph = unpack_from(">H", data, address + displacement + 2 * (code - begin))[0]
                    if glyph == 0:
                        continue
                self.glyphs[code] = (glyph + delta) % 65536

    def scale(self, value):
        return round(value * 1000 / self.units)

    def glyph(self, char):
        return self.glyphs.get(ord(char), self.glyphs[ord("?")])

    def advance(self, char):
        return self.advances[min(self.glyph(char), len(self.advances) - 1)]

    def wrap(self, line, limit):
        clean = "".join(c if ord(c) in self.glyphs and c.isprintable() else "?" for c in line)
        width, last_space, first = 0, None, 0
        for index, char in enumerate(clean):
            width += self.advance(char)
            if width > limit and index > first:
                cut = last_space + 1 if last_space is not None else index
                yield clean[first:cut].rstrip()
                first = cut
                width = sum(self.advance(c) for c in clean[cut:index + 1])
                last_space = None
            if char == " ":
                last_space = index
        yield clean[first:].strip()


@lru_cache(maxsize=1)
def document_font():
    return DocumentFont(FONT_PATH.read_bytes())


def stream(data, extra=""):
    blob = compress(data)
    return f"<< /Length {len(blob)} /Filter /FlateDecode {extra} >>\nstream\n".encode() + blob + b"\nendstream"


def to_unicode(codes):
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
             "/CMapName /NotoUnicode def", "/CMapType 2 def",
             "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
    for start in range(0, len(codes), 100):
        group = codes[start:start + 100]
        lines.extend([f"{len(group)} beginbfchar", *(f"<{c:04X}> <{c:04X}>" for c in group), "endbfchar"])
    lines.extend(["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"])
    return stream("\n".join(lines).encode("ascii"))


def pdf_file(entries):
    output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, entry in enumerate(entries, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(entry.encode("ascii") if isinstance(entry, str) else entry)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(entries) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(entries) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(output)


def render_document(title, lines):
    font = document_font()
    body = [wrapped for line in lines for wrapped in font.wrap(line, 42000)]
    pages = [body[i:i + 35] for i in range(0, len(body), 35)] or [[]]
    blocks = []
    for number, rows in enumerate(pages, 1):
        entries = [(17, 782, DEMO_HEADING), (10, 756, "Цифровой деканат · учебный прототип"),
                   (15, 718, title)]
        entries.extend((11, 682 - 17 * i, row) for i, row in enumerate(rows))
        entries.append((10, 48, f"Страница {number} из {len(pages)} · Без юридической силы"))
        blocks.append(entries)
    return render_pages(font, blocks)


def document_text(pdf):
    """Read the issued content from PDFs written by render_document."""
    if not pdf.startswith(b"%PDF-1.7\n") or len(pdf) > 8 * 1024 * 1024:
        return ()
    body = []
    for page in DOCUMENT_PAGE.finditer(pdf):
        end = page.end() + int(page[2])
        if not pdf.startswith(b"\nendstream\nendobj\n", end):
            return ()
        try:
            decoder = decompressobj()
            content = decoder.decompress(pdf[page.end():end], 512 * 1024)
            if not decoder.eof or decoder.unused_data:
                return ()
            commands = [DOCUMENT_LINE.fullmatch(line) for line in content.splitlines()]
            if not all(commands):
                return ()
            rows = [bytes.fromhex(command[1].decode("ascii")).decode("utf-16-be") for command in commands]
        except (ValueError, ZlibError):
            return ()
        if len(rows) < 4 or rows[0] != DEMO_HEADING or not rows[-1].endswith("· Без юридической силы"):
            return ()
        body.extend(rows[3:-1])
    return tuple(body)


def render_lecture(title, category, paragraphs, source):
    """Typeset lecture materials with Cyrillic text, margins and page numbers."""
    font = document_font()
    title_rows = list(font.wrap(title, 24000))
    heading = [(12, 782, category)]
    heading.extend((19, 750 - 26 * index, line) for index, line in enumerate(title_rows))
    y = 730 - 26 * len(title_rows)
    for line in font.wrap(source, 49000):
        heading.append((10, y, line))
        y -= 15
    y -= 18
    blocks, rows = [], list(heading)
    for paragraph in paragraphs:
        if paragraph.startswith("## "):
            if y < 145:
                blocks.append(rows)
                rows, y = [(12, 782, category)], 742
            y -= 5
            for line in font.wrap(paragraph[3:], 33500):
                if y < 100:
                    blocks.append(rows)
                    rows, y = [(12, 782, category)], 742
                rows.append((14, y, line))
                y -= 20
            continue
        if not paragraph.strip():
            continue
        for line in font.wrap(paragraph, 43500):
            if y < 85:
                blocks.append(rows)
                rows, y = [(12, 782, category)], 742
            rows.append((11, y, line))
            y -= 17
        y -= 10
    blocks.append(rows)
    for index, rows in enumerate(blocks, 1):
        rows.append((10, 48, f"Страница {index} из {len(blocks)}"))
    return render_pages(font, blocks)


def render_pages(font, blocks):
    codes = sorted({ord(char) for entries in blocks for _, _, row in entries for char in row})
    glyph_map = bytearray((max(codes) + 1) * 2)
    for code in codes:
        glyph_map[code * 2:code * 2 + 2] = pack(">H", font.glyph(chr(code)))
    widths = " ".join(f"{code} [{font.advance(chr(code))}]" for code in codes)
    bbox = " ".join(map(str, font.bbox))
    kids = " ".join(f"{9 + 2 * i} 0 R" for i in range(len(blocks)))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R /OpenAction [9 0 R /FitH null] >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(blocks)} >>",
        "<< /Type /Font /Subtype /Type0 /BaseFont /NotoSans-Regular /Encoding /Identity-H "
        "/DescendantFonts [4 0 R] /ToUnicode 8 0 R >>",
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /NotoSans-Regular "
        f"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        f"/FontDescriptor 5 0 R /CIDToGIDMap 7 0 R /W [{widths}] >>",
        "<< /Type /FontDescriptor /FontName /NotoSans-Regular /Flags 32 "
        f"/FontBBox [{bbox}] /ItalicAngle 0 /Ascent {font.ascent} /Descent {font.descent} "
        f"/CapHeight 714 /StemV 80 /FontFile2 6 0 R >>",
        stream(font.data, f"/Length1 {len(font.data)}"),
        stream(bytes(glyph_map)),
        to_unicode(codes),
    ]
    for number, rows in enumerate(blocks):
        page_id = 9 + 2 * number
        commands = [f"BT /F1 {size} Tf 1 0 0 1 54 {y} Tm <{text.encode('utf-16be').hex().upper()}> Tj ET"
                    for size, y, text in rows]
        objects.extend([
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>",
            stream("\n".join(commands).encode("ascii")),
        ])
    return pdf_file(objects)
