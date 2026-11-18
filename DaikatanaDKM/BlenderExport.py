import bpy, re, struct, zlib, bmesh
import numpy as np
from pathlib import Path
from .DKM_IO import write_dkm
from .DKMtypes import (
    DKMFile,
    DKMHeader,
    DKMTextureCoordinate,
    DKMTriangle,
    DKMTriangleVertex,
    DKMFrame,
    DKMGLCommand,
    DKMGLCommandVertex,
    DKMSurface,
    DKMAnimSeq,
)
from .DKMnormals import light_normal_indices
from .DaiWal import RGBToDaiWalData, WriteDaiWAL

def build_frame_names(count: int, use_markers: bool, start: int) -> list[str]:
    scene = bpy.context.scene
    markers = sorted(scene.timeline_markers, key=lambda m: m.frame) if use_markers else []

    names = []
    for i in range(count):
        name = f"frame{i}"
        if markers:
            tl_frame = start + i
            current = None
            for m in markers:
                if m.frame <= tl_frame:
                    current = m
                else:
                    break
            if current is not None:
                n = tl_frame - current.frame + 1
                suffix = f"{n:02d}"
                base = current.name.strip().replace(" ", "_")
                name = base[:15 - len(suffix)] + suffix
        names.append(name[:15])
    return names

def _material_image(mat):
    img = None
    if mat is not None:
        if mat.use_nodes and mat.node_tree:
            nodes = mat.node_tree.nodes
            active = nodes.active
            if active is not None and active.type == 'TEX_IMAGE' and active.image:
                img = active.image
            else:
                for n in nodes:
                    if n.type == 'TEX_IMAGE' and n.image:
                        img = n.image
                        break
        if img is None:
            img = bpy.data.images.get(mat.name) or bpy.data.images.get(Path(mat.name).name)
    return img

def _material_skin_size(mat) -> tuple[int, int]:
    img = _material_image(mat)
    if img is not None:
        w, h = img.size
        if w > 0 and h > 0:
            return int(w), int(h)
        print(f"Warning: image '{img.name}' has no pixel data; assuming 256x256.")
    return 256, 256

def _image_rgba8(img) -> np.ndarray:
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = np.clip(np.flipud(px.reshape(h, w, 4)), 0.0, 1.0)
    rgb, alpha = px[..., :3], px[..., 3:]
    if img.is_float:
        rgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    return np.rint(np.concatenate([rgb, alpha], axis=2) * 255.0).astype(np.uint8)

#Texture writers
#Only TGA uses the alpha channel.
def _write_wal(out: Path, rgba: np.ndarray) -> str:
    mipmaps, palette = RGBToDaiWalData(rgba[..., :3])
    WriteDaiWAL(out, out.stem[:31], mipmaps, palette)
    return f", {len(mipmaps)} mip levels"

def _write_pcx(out: Path, rgba: np.ndarray) -> str:
    h, w = rgba.shape[:2]
    mipmaps, palette = RGBToDaiWalData(rgba[..., :3]) #only mip 0 is used; PCX is always 8-bit paletted here
    bpl = w + (w & 1) #bytes per line must be even
    header = bytearray(128)
    header[0:4] = bytes([10, 5, 1, 8])
    struct.pack_into("<4H", header, 4, 0, 0, w - 1, h - 1)
    struct.pack_into("<2H", header, 12, 72, 72)
    header[65] = 1
    struct.pack_into("<2H", header, 66, bpl, 1)
    body = bytearray()
    for row in mipmaps[0]:
        line = bytes(row) + b"\x00" * (bpl - w)
        i = 0
        while i < bpl:
            j = i
            while j < bpl and line[j] == line[i] and j - i < 63:
                j += 1
            run = j - i
            if run > 1 or line[i] >= 0xC0:
                body += bytes([0xC0 | run, line[i]])
            else:
                body.append(line[i])
            i = j
    out.write_bytes(bytes(header) + bytes(body) + b"\x0c" + np.asarray(palette, dtype=np.uint8).tobytes())
    return ", 256 colours"

def _tga_rle_row(row: np.ndarray) -> bytes:
    #Run-length encodes one scanline (W, channels). Packets never span scanlines.
    n = row.shape[0]
    change = np.flatnonzero((row[1:] != row[:-1]).any(axis=1)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [n]))
    out = bytearray()

    def flush_raw(a: int, b: int):
        while a < b:
            c = min(128, b - a)
            out.append(c - 1)
            out.extend(row[a:a + c].tobytes())
            a += c

    raw_start = None
    for s, e in zip(starts.tolist(), ends.tolist()):
        if e - s == 1:
            if raw_start is None:
                raw_start = s
            continue
        if raw_start is not None:
            flush_raw(raw_start, s)
            raw_start = None
        pixel, left = row[s].tobytes(), e - s
        while left > 0:
            c = min(128, left)
            out.append(0x80 | (c - 1))
            out.extend(pixel)
            left -= c
    if raw_start is not None:
        flush_raw(raw_start, n)
    return bytes(out)

def _write_tga(out: Path, rgba: np.ndarray) -> str:
    #Opaque images become type 2 (uncompressed, 24-bit); images with transparent or translucent pixels
    #become type 10 (run-length encoded, 32-bit with alpha). Origin is bottom-left.
    h, w = rgba.shape[:2]
    translucent = bool((rgba[..., 3] < 255).any())
    if translucent:
        pix = np.ascontiguousarray(np.flipud(rgba[..., [2, 1, 0, 3]]))
        header = struct.pack("<BBBHHBHHHHBB", 0, 0, 10, 0, 0, 0, 0, 0, w, h, 32, 8)
        out.write_bytes(header + b"".join(_tga_rle_row(row) for row in pix))
        return ", type 10, 32-bit with alpha"
    pix = np.flipud(rgba[..., [2, 1, 0]])
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 24, 0)
    out.write_bytes(header + pix.tobytes())
    return ", type 2, 24-bit"

def _write_bmp(out: Path, rgba: np.ndarray) -> str:
    h, w = rgba.shape[:2]
    mipmaps, palette = RGBToDaiWalData(rgba[..., :3]) #8-bit paletted, bottom-up
    pad = (-w) % 4
    pix = np.flipud(mipmaps[0])
    if pad:
        pix = np.concatenate([pix, np.zeros((h, pad), dtype=np.uint8)], axis=1)
    data = pix.tobytes()
    colours = np.zeros((256, 4), dtype=np.uint8) #BGRA entries
    colours[:, :3] = np.asarray(palette, dtype=np.uint8)[:, ::-1]
    offset = 14 + 40 + 1024
    out.write_bytes(struct.pack("<2sIHHI", b"BM", offset + len(data), 0, 0, offset) +
                    struct.pack("<IiiHHIIiiII", 40, w, h, 1, 8, 0, len(data), 2835, 2835, 256, 256) +
                    colours.tobytes() + data)
    return ", 256 colours"

def _write_png(out: Path, rgba: np.ndarray) -> str:
    #Raw 32-bit (8-bit RGBA) PNG, so all colours and translucency are kept. The game can't load PNGs, so no palette.
    h, w = rgba.shape[:2]
    raw = np.concatenate([np.zeros((h, 1), dtype=np.uint8), rgba.reshape(h, w * 4)], axis=1).tobytes()
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    out.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
                    chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    return ""

TEXTURE_WRITERS = {".wal": _write_wal, ".pcx": _write_pcx, ".tga": _write_tga, ".bmp": _write_bmp, ".png": _write_png}

def _export_textures(folder: Path, skinpaths: list, materials: list):
    #Writes each skin's texture into `folder`, in the format of the skin path's extension.
    done = set()
    for skin, mat in zip(skinpaths, materials):
        if mat is None: #the default skin has no material
            continue
        leaf = Path(skin.replace("\\", "/"))
        writer = TEXTURE_WRITERS.get(leaf.suffix.lower())
        if not leaf.stem or writer is None:
            print(f"Texture: skin '{skin}' doesn't end in a supported extension ({', '.join(TEXTURE_WRITERS)}), skipped.")
            continue
        if leaf.name.lower() in done:
            print(f"Texture: '{leaf.name}' was already written for another skin, skipped '{skin}'.")
            continue
        done.add(leaf.name.lower())
        img = _material_image(mat)
        if img is None:
            print(f"Texture: no image found for material '{mat.name}', skipped.")
            continue
        w, h = img.size
        if w == 0 or h == 0:
            print(f"Texture: image '{img.name}' has no pixel data, skipped.")
            continue
        if leaf.suffix.lower() == ".wal" and ((w & (w - 1)) or (h & (h - 1))):
            print(f"Warning: '{img.name}' is {w}x{h}, not a power of two; mip levels will have odd sizes.")
        note = writer(folder / leaf.name, _image_rgba8(img))
        print(f"Texture: wrote '{leaf.name}' ({w}x{h}{note}).")

def _build_sequences(count: int, option: int, start: int, end: int) -> list:
    if option == 0 or count == 0:
        return []
    if option == 1:
        return [DKMAnimSeq(name="Default", startFrame=0, endFrame=count - 1)]

    def clean(n):
        return n.strip().replace(" ", "_")[:15]

    markers = sorted(bpy.context.scene.timeline_markers, key=lambda m: m.frame)
    current = None
    for m in markers:
        if m.frame <= start:
            current = m
    starts = [(0, clean(current.name) if current else "Default")]
    for m in markers:
        if start < m.frame <= end and m.frame - start != starts[-1][0]:
            starts.append((m.frame - start, clean(m.name)))

    seqs = []
    for i, (s_frame, name) in enumerate(starts):
        e_frame = starts[i + 1][0] - 1 if i + 1 < len(starts) else count - 1
        seqs.append(DKMAnimSeq(name=name, startFrame=s_frame, endFrame=e_frame))
    return seqs

def _build_gl_commands(dkm, surfaces, surface_tris, surf_global, voff):
    for local_idx, tris_in_surf in surface_tris.items():
        if not tris_in_surf:
            continue

        surf_info = surfaces[local_idx]
        s_w = surf_info.skinWidth if surf_info.skinWidth > 0 else 256
        s_h = surf_info.skinHeight if surf_info.skinHeight > 0 else 256

        fv = [tuple(t[1].vertices) for t in tris_in_surf]
        fu = [tuple(t[2]) for t in tris_in_surf]
        num_faces = len(fv)

        edge_map = {}
        for t in range(num_faces):
            for k in range(3):
                key = (fv[t][k], fv[t][(k + 1) % 3], fu[t][k], fu[t][(k + 1) % 3])
                edge_map.setdefault(key, []).append((t, k))

        used = [False] * num_faces

        def next_tri(m1, m2, u1, u2, in_cmd):
            for t, k in edge_map.get((m1, m2, u1, u2), ()):
                if not used[t] and t not in in_cmd:
                    return t, k
            return None

        def try_strip(t0, s):
            m1, m2 = fv[t0][(s + 2) % 3], fv[t0][(s + 1) % 3]
            u1, u2 = fu[t0][(s + 2) % 3], fu[t0][(s + 1) % 3]
            verts = [fv[t0][s], m1, m2]
            uvs = [fu[t0][s], u1, u2]
            tris = [t0]
            in_cmd = {t0}
            while True:
                hit = next_tri(m1, m2, u1, u2, in_cmd)
                if hit is None:
                    break
                t, k = hit
                nv, nu = fv[t][(k + 2) % 3], fu[t][(k + 2) % 3]
                if len(tris) % 2 == 1:
                    m1, u1 = nv, nu
                else:
                    m2, u2 = nv, nu
                verts.append(nv)
                uvs.append(nu)
                tris.append(t)
                in_cmd.add(t)
            return tris, verts, uvs

        def try_fan(t0, s):
            hub, hub_u = fv[t0][s], fu[t0][s]
            m1, u1 = fv[t0][(s + 1) % 3], fu[t0][(s + 1) % 3]
            verts = [hub, fv[t0][(s + 2) % 3], m1]
            uvs = [hub_u, fu[t0][(s + 2) % 3], u1]
            tris = [t0]
            in_cmd = {t0}
            while True:
                hit = next_tri(m1, hub, u1, hub_u, in_cmd)
                if hit is None:
                    break
                t, k = hit
                nv, nu = fv[t][(k + 2) % 3], fu[t][(k + 2) % 3]
                m1, u1 = nv, nu
                verts.append(nv)
                uvs.append(nu)
                tris.append(t)
                in_cmd.add(t)
            return tris, verts, uvs

        for i in range(num_faces):
            if used[i]:
                continue

            best = None
            for s in range(3):
                for is_fan, trial in ((False, try_strip), (True, try_fan)):
                    tris, verts, uvs = trial(i, s)
                    if best is None or len(tris) > len(best[1]):
                        best = (is_fan, tris, verts, uvs)

            is_fan, tris, verts, uvs = best
            for t in tris:
                used[t] = True

            cmd_verts = []
            for v_idx, uv_idx in zip(verts, uvs):
                uv_coord = dkm.UV[uv_idx]
                cmd_verts.append(DKMGLCommandVertex(
                    vertexIndex=v_idx + voff,
                    s=float(uv_coord.s) / s_w,
                    t=float(uv_coord.t) / s_h))

            dkm.GLCommands.append(DKMGLCommand(
                count=-len(cmd_verts) if is_fan else len(cmd_verts),
                skinIndex=surf_info.skinIndex,
                surfIndex=surf_global[local_idx],
                vertices=cmd_verts))

def export_dkm_file(filepath, objs, ModelVersion: int, origin: list[float], SurfacesFromVertexGroups: bool, ExportAnimations: bool, MarkerFrameNames: bool, StartAnimFrame: int, EndAnimFrame: int, AnimSeqOptions: int, ExportTextures: bool):
    filepath = Path(filepath)
    if isinstance(objs, bpy.types.Object):
        objs = [objs]
    objs = [o for o in objs if o and o.type == 'MESH']
    if not objs:
        raise RuntimeError("No valid mesh objects to export.")
    if not ExportAnimations:
        StartAnimFrame = EndAnimFrame = bpy.context.scene.frame_current
        MarkerFrameNames = False
        AnimSeqOptions = 0
    if EndAnimFrame < StartAnimFrame:
        raise RuntimeError("End frame must be >= start frame.")

    scene = bpy.context.scene
    original_frame = scene.frame_current
    temp_meshes = []
    base_meshes = [None] * len(objs)
    obj_coords = [[] for _ in objs]

    try:
        try:
            for f in range(StartAnimFrame, EndAnimFrame + 1):
                scene.frame_set(f)
                depsgraph = bpy.context.evaluated_depsgraph_get()
                for i, obj in enumerate(objs):
                    eval_obj = obj.evaluated_get(depsgraph)
                    me = bpy.data.meshes.new_from_object(
                        eval_obj, preserve_all_data_layers=True, depsgraph=depsgraph)

                    if base_meshes[i] is None:
                        base_meshes[i] = me
                        temp_meshes.append(me)
                    elif len(me.vertices) != len(base_meshes[i].vertices):
                        bpy.data.meshes.remove(me)
                        raise RuntimeError(
                            f"'{obj.name}': vertex count changes at frame {f} "
                            f"({len(me.vertices)} vs {len(base_meshes[i].vertices)}). "
                            f"DKM needs a constant count.")

                    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
                    me.vertices.foreach_get("co", co)
                    co = co.reshape(-1, 3)
                    m = np.array(eval_obj.matrix_world)
                    obj_coords[i].append(co @ m[:3, :3].T + m[:3, 3])

                    if me is not base_meshes[i]:
                        bpy.data.meshes.remove(me)
        finally:
            scene.frame_set(original_frame)

        frame_coords = [np.vstack([obj_coords[i][k] for i in range(len(objs))])
                        for k in range(EndAnimFrame - StartAnimFrame + 1)]
        total_verts = len(frame_coords[0])
        if total_verts > 32767:
            raise RuntimeError(f"Combined vertex count is {total_verts}; the DKM limit is 32767.")

        dkm = DKMFile()
        dkm.header.ident = int.from_bytes(b"DKMD", "little")
        dkm.header.version = ModelVersion
        dkm.header.origin = origin
        dkm.header.numVertices = total_verts
        vert_size = 4 if ModelVersion == 1 else 5
        dkm.header.frameSize = 40 + total_verts * vert_size + (3 if ModelVersion == 2 else 0)

        vg_pattern = re.compile(r'^DKM__(.*)__(.+)\.(.+)$')
        skinpaths = []
        skin_sizes = []
        skin_materials = [] #the material behind each skin path, for exporting textures

        def skin_size(idx):
            return skin_sizes[idx] if 0 <= idx < len(skin_sizes) else (256, 256)

        uv_dict = {}
        surface_lookup = {}

        def fuse_surface(surf):
            key = (surf.name, surf.skinIndex, surf.flags)
            if key not in surface_lookup:
                surface_lookup[key] = len(dkm.surfaces)
                dkm.surfaces.append(surf)
            return surface_lookup[key]
        vert_offset = 0

        all_tris = []
        for obj, mesh in zip(objs, base_meshes):
            def global_skin(mat):
                name = re.sub(r"\.\d{3}$", "", mat.name) if mat else "default_skin.tga" #drop Blender's duplicate suffix
                if name not in skinpaths:
                    skinpaths.append(name)
                    skin_sizes.append(_material_skin_size(mat))
                    skin_materials.append(mat)
                return skinpaths.index(name)

            skin_map = [global_skin(mat) for mat in mesh.materials]
            if not skin_map:
                skin_map = [global_skin(None)]

            def map_skin(local_idx):
                return skin_map[local_idx] if 0 <= local_idx < len(skin_map) else local_idx

            surfaces = []
            surf_global = []
            surface_vertex_maps = {}
            for vg in (obj.vertex_groups if SurfacesFromVertexGroups else []):
                match = vg_pattern.match(vg.name)
                if match:
                    surf_idx = len(surfaces)
                    skin_idx = map_skin(int(match.group(2)))
                    sw, sh = skin_size(skin_idx)
                    surf = DKMSurface(
                        name=match.group(1), flags=int(match.group(3)),
                        skinIndex=skin_idx, skinWidth=sw, skinHeight=sh, numUVframes=0)
                    surfaces.append(surf)
                    surf_global.append(fuse_surface(surf))
                    surface_vertex_maps[surf_idx] = {
                        v.index for v in mesh.vertices
                        if any(g.group == vg.index and g.weight > 0.0 for g in v.groups)}

            if not surfaces:
                skin_idx = map_skin(0)
                sw, sh = skin_size(skin_idx)
                surf = DKMSurface(
                    name=obj.name[:31] if len(objs) > 1 else "default_surf",
                    flags=0, skinIndex=skin_idx, skinWidth=sw, skinHeight=sh, numUVframes=0)
                surfaces.append(surf)
                surf_global.append(fuse_surface(surf))
                surface_vertex_maps[0] = set(range(len(mesh.vertices)))

            bm = bmesh.new()
            bm.from_mesh(mesh)
            bmesh.ops.triangulate(bm, faces=bm.faces)
            tri_mesh = bpy.data.meshes.new("tri_temp")
            temp_meshes.append(tri_mesh)
            bm.to_mesh(tri_mesh)
            bm.free()
            tri_mesh.calc_loop_triangles()

            if not tri_mesh.uv_layers:
                raise RuntimeError(f"'{obj.name}' has no UV layer, required for DKM export.")
            uv_layer = tri_mesh.uv_layers.active.data

            surface_tris = {i: [] for i in range(len(surfaces))}
            for tri_idx, tri in enumerate(tri_mesh.loop_triangles):
                poly_verts = set(tri.vertices)
                assigned = 0
                for s_idx, v_set in surface_vertex_maps.items():
                    if poly_verts.intersection(v_set):
                        assigned = s_idx
                        break

                surf_info = surfaces[assigned]
                s_w = surf_info.skinWidth if surf_info.skinWidth > 0 else 256
                s_h = surf_info.skinHeight if surf_info.skinHeight > 0 else 256

                tri_tex_indices = []
                for l_idx in tri.loops:
                    uv = uv_layer[l_idx].uv
                    key = (int(round(uv.x * s_w)), int(round((1.0 - uv.y) * s_h)))
                    if key not in uv_dict:
                        uv_dict[key] = len(dkm.UV)
                        dkm.UV.append(DKMTextureCoordinate(s=key[0], t=key[1]))
                    tri_tex_indices.append(uv_dict[key])

                v = [vi + vert_offset for vi in tri.vertices]
                all_tris.append(v) #regular winding, for normals
                dkm.triangles.append(DKMTriangle(
                    surfaceIndex=surf_global[assigned],
                    num_uvframes=1,
                    vertexIndices=[v[0], v[2], v[1]], #reversed winding
                    textureIndices=[tri_tex_indices[0], tri_tex_indices[2], tri_tex_indices[1]]))
                surface_tris[assigned].append((tri_idx, tri, tri_tex_indices))

            _build_gl_commands(dkm, surfaces, surface_tris, surf_global, vert_offset)

            vert_offset += len(mesh.vertices)

        dkm.skinpaths = skinpaths

        fan_count = sum(1 for c in dkm.GLCommands if c.count < 0)
        strip_count = len(dkm.GLCommands) - fan_count
        print(f"Created {len(dkm.GLCommands)} GL Commands: {strip_count} strips and {fan_count} fans.")

        qmax = np.array([255, 255, 255] if ModelVersion == 1 else [2047, 1023, 2047], dtype=np.float64)
        names = build_frame_names(len(frame_coords), MarkerFrameNames, StartAnimFrame)

        all_tris = np.array(all_tris, dtype=np.int64).reshape(-1, 3)
        frames = []
        for name, co in zip(names, frame_coords):
            normal_idx = light_normal_indices(co, all_tris)
            mn = co.min(axis=0)
            rng = co.max(axis=0) - mn
            scale = np.where(rng > 0, rng / qmax, 1.0)
            q = np.clip(np.rint((co - mn) / scale), 0, qmax).astype(int)
            frame_verts = [DKMTriangleVertex(vertex=[int(a), int(b), int(c)],
                                             lightNormalIndex=int(n))
                           for (a, b, c), n in zip(q, normal_idx)]
            frames.append(DKMFrame(scale=[float(s) for s in scale],
                                   translate=[float(t) for t in mn],
                                   name=name, vertices=frame_verts, unknown=[0, 0, 0]))
        dkm.frames = frames
        dkm.sequences = _build_sequences(len(frames), AnimSeqOptions, StartAnimFrame, EndAnimFrame)

        write_dkm(dkm, filepath)
        if ExportTextures:
            _export_textures(filepath.parent, skinpaths, skin_materials)
        print(f"Successfully exported {len(objs)} object(s) to '{filepath.name}'.")
        return True

    finally:
        for m in temp_meshes:
            bpy.data.meshes.remove(m)

