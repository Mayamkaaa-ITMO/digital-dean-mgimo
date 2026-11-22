"""Read clean, horizontal Cyrillic timetable rows by matching printed glyphs.

A last-resort local reader for flat scans. The caller still requires manual review.
Font prototypes are derived from the system fonts at runtime.
"""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import json
import statistics
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps

ALPHABET = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя0123456789:.,—–-/()"
FAMILIES = ("Arial", "Arial Bold", "Verdana", "Trebuchet MS", "Times New Roman",
            "Georgia", "DejaVuSans", "DejaVuSans-Bold", "LiberationSans-Regular")


def runs(counts):
    start = None
    for i, n in enumerate([*counts, 0]):
        if n and start is None:
            start = i
        elif not n and start is not None:
            yield start, i
            start = None


def bitmap(image):
    # Bitsets keep the search small enough to run without numerical libraries.
    thumb = image.resize((14, 20), Image.Resampling.BILINEAR).point(lambda x: 255 if x > 100 else 0).convert("1")
    return int.from_bytes(thumb.tobytes(), "big")


@lru_cache(maxsize=1)
def prototypes():
    paths = [Path("/System/Library/Fonts/Supplemental"), Path("/usr/share/fonts/truetype/dejavu"),
             Path("/usr/share/fonts/truetype/liberation2")]
    pool = []
    for family in FAMILIES:
        path = next((p for root in paths if (p := root / (family + ".ttf")).is_file()), None)
        if not path:
            continue
        for size in (18, 26, 34):
            font = ImageFont.truetype(str(path), size)
            ref = -font.getbbox("Н", anchor="ls")[1]
            for char in ALPHABET:
                mask = Image.new("L", (size*2, size*2))
                ImageDraw.Draw(mask).text((4, size), char, font=font, fill=255, anchor="ls")
                bounds = mask.point(lambda x: 255 if x>100 else 0).getbbox()
                if not bounds:
                    continue
                l,t,r,b = bounds
                pool.append((char, bitmap(mask.crop(bounds)), (r-l)/(b-t), (b-t)/ref, (size-b)/ref))
    return pool


def recognize(data):
    with Image.open(BytesIO(data)) as source:
        if source.width*source.height > 10_000_000:
            return []
        gray = ImageOps.exif_transpose(source).convert("L")
    gray.thumbnail((2300, 2800))
    w, h = gray.size
    if not w or not h:
        return []
    mask = gray.point(lambda x: 255 if x<145 else 0)
    draw = ImageDraw.Draw(mask)
    row_counts = list(mask.resize((1,h),Image.Resampling.BOX).getdata())
    for begin,end in runs(n >= 180 for n in row_counts):
        draw.rectangle((0,begin,w,end-1),fill=0)
    col_counts = list(mask.resize((w,1),Image.Resampling.BOX).getdata())
    for begin,end in runs(n >= 105 for n in col_counts):
        draw.rectangle((begin,0,end-1,h),fill=0)
    pool=prototypes()
    result=[]
    if not pool:
        return result
    rows=list(runs(mask.getprojection()[1]))
    # The dots above й/ё can be disconnected vertically from the rest of the line.
    merged=[]
    for t,b in rows:
        if merged and t-merged[-1][1] <= 3 and merged[-1][1]-merged[-1][0] <= 6:
            merged[-1]=(merged[-1][0],b)
        else:
            merged.append((t,b))
    for t,b in merged:
        if not 8 <= b-t <= 110:
            continue
        strip=mask.crop((0,t,w,b))
        letters=[]
        for l,r in runs(strip.getprojection()[0]):
            box=strip.crop((l,0,r,b-t)).getbbox()
            if box:
                letters.append((l,box[1],r,box[3]))
        if not 3 <= len(letters) <= 220:
            continue
        sizes=sorted(bottom-top for _,top,_,bottom in letters)
        cap=sizes[min(len(sizes)-1,int(len(sizes)*.87))]
        base=statistics.median(bottom for _,top,_,bottom in letters if bottom-top >= .6*cap)
        parts=[]
        prev=None
        for l,top,r,bottom in letters:
            wid,ht=r-l,bottom-top
            if prev is not None and l-prev > max(2,cap*.27):
                parts.append(" ")
            prev=r
            bits=bitmap(strip.crop((l,top,r,bottom)))
            aspect=wid/ht
            rel=ht/cap
            drop=(base-bottom)/cap
            def cost(item):
                _,template,asp,height,baseline=item
                return ((bits^template).bit_count()/280 + .14*abs(aspect-asp)/max(.25,asp,aspect)
                        +.43*abs(rel-height) + .40*abs(drop-baseline))
            parts.append(min(pool,key=cost)[0])
        result.append(dict(text="".join(parts), x=letters[0][0]/w,y=(t+b)/2/h,h=(b-t)/h))
    return result


if __name__ == "__main__":
    print(json.dumps(recognize(sys.stdin.buffer.read()),ensure_ascii=False))
