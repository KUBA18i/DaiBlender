import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
import numpy as np

DaiMIPs: Final = 9

@dataclass
class DaiWal_colordef:
    R: int = 0
    G: int = 0
    B: int = 0

@dataclass
class DaiWal_header:
    version: int = 0  #one byte, should be 3
    name: str = ""  #32 char limit
    Width: int = 0  #uint32_t
    Height: int = 0  #uint32_t
    Offsets: list = field(default_factory=list)  #array of uint32_t
    AnimName: str = ""  #32 char limit
    Flags: int = 0
    Contents: int = 0
    palette: list = field(default_factory=list)  #array of DaiWal_colordef
    Value: int = 0

def ReadDaiWAL(inpath: str | Path):
    inpath = Path(inpath)
    print(f"Loading DaiWAL texture: \"{inpath}\"")
    NewDWHeader = DaiWal_header()

    with open(inpath, "rb") as f:
        NewDWHeader.version = struct.unpack("B", f.read(1))[0]
        NewDWHeader.name = f.read(32).split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
        f.read(3)  #skip over padding
        NewDWHeader.Width, NewDWHeader.Height = struct.unpack("2I", f.read(8))
        NewDWHeader.Offsets = list(struct.unpack(f"{DaiMIPs}I", f.read(DaiMIPs * 4)))
        NewDWHeader.AnimName = f.read(32).split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
        NewDWHeader.Flags, NewDWHeader.Contents = struct.unpack("2i", f.read(8))
        for _ in range(256):
            colour = DaiWal_colordef()
            colour.R, colour.G, colour.B = struct.unpack("3B", f.read(3))
            NewDWHeader.palette.append(colour)
        NewDWHeader.Value = struct.unpack("i", f.read(4))[0]
        PixelArraySize = NewDWHeader.Width * NewDWHeader.Height
        if NewDWHeader.Offsets[0]:
            f.seek(NewDWHeader.Offsets[0])
        data = f.read(PixelArraySize)
        if len(data) != PixelArraySize:
            raise RuntimeError(f"'{inpath.name}' is truncated: expected {PixelArraySize} pixels, found {len(data)}.")
        pixels = np.frombuffer(data, dtype=np.uint8)
    return NewDWHeader, pixels

def DaiWalToRGBA(inpath: str | Path):
    header, pixels = ReadDaiWAL(inpath)
    W, H = header.Width, header.Height
    palette = np.array([[c.R, c.G, c.B] for c in header.palette], dtype=np.uint8)
    rgb = palette[pixels.reshape(H, W)]
    rgba = np.ones((H, W, 4), dtype=np.float32)
    rgba[..., :3] = rgb / 255.0
    rgba = np.flipud(rgba)
    return W, H, rgba.ravel()

def WriteDaiWAL(outpath: str | Path, name: str, mipmaps: list, palette: np.ndarray):
    mipmaps = mipmaps[:DaiMIPs]
    offsets = [0] * DaiMIPs
    offset = 892
    for i, m in enumerate(mipmaps):
        offsets[i] = offset
        offset += m.size
    for i in range(len(mipmaps), DaiMIPs):
        offsets[i] = offsets[len(mipmaps) - 1]  #unused slots point at the smallest mip
    height, width = mipmaps[0].shape
    with open(outpath, "wb") as f:
        f.write(struct.pack("B", 3))
        f.write(name.encode("ascii", errors="ignore").ljust(32, b"\x00")[:32])
        f.write(B"XXX")  #padding
        f.write(struct.pack("2I", width, height))
        f.write(struct.pack(f"{DaiMIPs}I", *offsets))
        f.write(b"Exported by Python script".ljust(32, b"\x00"))
        f.write(struct.pack("2i", 0, 0))  #flags, contents
        f.write(np.asarray(palette, dtype=np.uint8).tobytes())
        f.write(struct.pack("i", 0))  #value
        for m in mipmaps:
            f.write(m.tobytes())

def _median_cut(colors, counts, n=256):
    if len(colors) <= n:
        return colors
    boxes = [np.arange(len(colors))]
    while len(boxes) < n:
        best, best_score = None, -1
        for bi, idx in enumerate(boxes):
            if len(idx) < 2:
                continue
            c = colors[idx].astype(np.int32)
            score = counts[idx].sum() * (c.max(0) - c.min(0)).max()
            if score > best_score:
                best, best_score = bi, score
        if best is None:
            break
        idx = boxes.pop(best)
        c = colors[idx].astype(np.int32)
        ch = int(np.argmax(c.max(0) - c.min(0)))
        order = idx[np.argsort(colors[idx, ch], kind="stable")]
        cum = np.cumsum(counts[order])
        cut = int(np.searchsorted(cum, cum[-1] / 2)) + 1
        cut = min(max(cut, 1), len(order) - 1)
        boxes += [order[:cut], order[cut:]]
    pal = [np.average(colors[idx], axis=0, weights=counts[idx]) for idx in boxes]
    return np.rint(np.array(pal)).astype(np.uint8)

def _nearest(pal, px):
    p = pal.astype(np.float32)
    p2 = (p * p).sum(axis=1)
    out = np.empty(len(px), dtype=np.uint8)
    for i in range(0, len(px), 65536):
        c = px[i:i + 65536]
        out[i:i + 65536] = np.argmin(p2[None, :] - 2.0 * (c @ p.T), axis=1)
    return out

def _halve(a):
    h, w = a.shape[:2]
    if h > 1:
        a = (a[0:h - h % 2:2] + a[1:h - h % 2:2]) * 0.5
    if w > 1:
        a = (a[:, 0:w - w % 2:2] + a[:, 1:w - w % 2:2]) * 0.5
    return a

def RGBToDaiWalData(rgb: np.ndarray):
    keys = ((rgb[..., 0].astype(np.uint32) << 16) |
            (rgb[..., 1].astype(np.uint32) << 8) |
             rgb[..., 2].astype(np.uint32)).ravel()
    uniq, counts = np.unique(keys, return_counts=True)
    colors = np.stack([(uniq >> 16) & 255, (uniq >> 8) & 255, uniq & 255], axis=1).astype(np.uint8)
    used_pal = _median_cut(colors, counts, 256)

    palette = np.full((256, 3), (255, 0, 255), dtype=np.uint8) #unused slots become magenta
    palette[:len(used_pal)] = used_pal

    level = rgb.astype(np.float32)
    mipmaps = []
    while True:
        h, w = level.shape[:2]
        mipmaps.append(_nearest(used_pal, level.reshape(-1, 3)).reshape(h, w))
        if (h == 1 and w == 1) or len(mipmaps) == DaiMIPs:
            break
        level = _halve(level)
    return mipmaps, palette
