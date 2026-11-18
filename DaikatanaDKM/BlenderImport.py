import bpy, re, struct, bmesh
import numpy as np
from pathlib import Path
from typing import Final
from .DKM_IO import read_dkm
from .DaiWal import DaiWalToRGBA

TEXTURE_EXTENSIONS: Final = (".wal", ".pcx", ".tga", ".bmp", ".png")

def _find_in_dir(folder: Path, stem: str):
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    matches = [f for f in entries
               if f.is_file() and f.suffix.lower() in TEXTURE_EXTENSIONS and f.stem.lower() == stem]
    if not matches:
        return None
    matches.sort(key=lambda f: TEXTURE_EXTENSIONS.index(f.suffix.lower()))
    return matches[0]

def _descend(root: Path, parts: list):
    cur = root
    for part in parts:
        if part == ".":
            continue
        direct = cur / part
        if direct.is_dir():
            cur = direct
            continue
        try:
            nxt = next((d for d in cur.iterdir() if d.is_dir() and d.name.lower() == part.lower()), None)
        except OSError:
            return None
        if nxt is None:
            return None
        cur = nxt
    return cur

def _find_skin_file(skin_path: str, model_path: Path):
    parts = [p for p in skin_path.replace("\\", "/").split("/") if p]
    if not parts:
        return None
    stem = Path(parts[-1]).stem.lower() #the extension in the skin path is ignored; it's usually wrong

    #Attempt 1: a texture with the skin's name (any extension) in the same folder as the model.
    norm = str(model_path).replace("\\", "/")
    found = _find_in_dir(Path(norm).parent, stem)
    if found is not None:
        print(f"Skin '{skin_path}': found next to the model ({found.name}).")
        return found

    #Attempt 2: the part of the model's path before '/models/' + the skin path (like the game does)
    #(every '/models/' in the path is tried, in case a parent folder is also called 'models')
    low = norm.lower()
    start = 0
    while True:
        idx = low.find("/models/", start)
        if idx < 0:
            break
        root_str = norm[:idx]
        if not root_str or root_str.endswith(":"):
            root_str += "/"
        folder = _descend(Path(root_str), parts[:-1])
        if folder is not None:
            found = _find_in_dir(folder, stem)
            if found is not None:
                print(f"Skin '{skin_path}': found in game folder ({found}).")
                return found
        start = idx + 1
    return None

def _read_pcx(path: Path):
    data = Path(path).read_bytes()
    if len(data) < 128 or data[0] != 10 or data[2] != 1:
        raise ValueError("not an RLE-compressed PCX file")
    bpp, nplanes = data[3], data[65]
    xmin, ymin, xmax, ymax = struct.unpack_from("<4H", data, 4)
    bpl = struct.unpack_from("<H", data, 66)[0]
    if bpp != 8 or nplanes not in (1, 3):
        raise ValueError(f"unsupported PCX layout ({bpp} bpp, {nplanes} planes)")
    w, h = xmax - xmin + 1, ymax - ymin + 1
    total = bpl * nplanes * h

    out = bytearray(total)
    pos, n, size = 128, 0, len(data)
    while n < total and pos < size:
        b = data[pos]
        pos += 1
        if b >= 0xC0:
            if pos >= size:
                break
            run = min(b & 0x3F, total - n)
            out[n:n + run] = bytes([data[pos]]) * run
            pos += 1
            n += run
        else:
            out[n] = b
            n += 1
    if n < total:
        raise ValueError("PCX pixel data is truncated")

    arr = np.frombuffer(out, dtype=np.uint8)
    if nplanes == 1:
        if size < 769 or data[-769] != 12:
            raise ValueError("PCX has no 256 colour palette")
        palette = np.frombuffer(data, dtype=np.uint8, count=768, offset=size - 768).reshape(256, 3)
        rgb = palette[arr.reshape(h, bpl)[:, :w]]
    else:
        rgb = arr.reshape(h, 3, bpl)[:, :, :w].transpose(0, 2, 1)
    rgba = np.ones((h, w, 4), dtype=np.float32)
    rgba[..., :3] = rgb / 255.0
    return w, h, np.flipud(rgba).ravel()

def _load_texture_image(path: Path):
    name = path.name[:63]
    img = bpy.data.images.get(name)
    if img is not None:
        return img
    ext = path.suffix.lower()
    try:
        if ext in (".wal", ".pcx"):
            w, h, rgba = DaiWalToRGBA(path) if ext == ".wal" else _read_pcx(path)
            img = bpy.data.images.new(name, width=w, height=h, alpha=False)
            img.pixels.foreach_set(rgba)
            img.update()
        else:
            img = bpy.data.images.load(str(path))
            img.name = name
        img.pack()
    except Exception as e:
        print(f"Warning: couldn't read '{path}': {e}")
        return None
    return img

def _get_or_create_material(skin_path: str, model_path: Path, ImportTextures: bool):
    name = (skin_path or "DKM_Material")[:63]
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        nodes.clear()
        diffuse = nodes.new(type='ShaderNodeBsdfDiffuse')
        out = nodes.new(type='ShaderNodeOutputMaterial')
        out.location = (200, 0)
        links.new(diffuse.outputs['BSDF'], out.inputs['Surface'])
        if ImportTextures and skin_path:
            tex_path = _find_skin_file(skin_path, model_path)
            if tex_path is None:
                print(f"No texture found for skin '{skin_path}'.")
            else:
                img = _load_texture_image(tex_path)
                if img is not None:
                    tex = nodes.new(type='ShaderNodeTexImage')
                    tex.image = img
                    tex.interpolation = 'Closest'
                    tex.location = (-300, 0)
                    links.new(tex.outputs['Color'], diffuse.inputs['Color'])
    return mat

def import_dkm_file(filepath: str | Path, base_frame_index: int, UVsFromGLCommands: bool, ImportAnimations: bool, TimelineMarkerOptions: int, ImportTextures: bool = True):
    filepath = Path(filepath)
    dkm_data = read_dkm(filepath)

    if not dkm_data.frames:
        print("Error: No frames found in the DKM file.")
        return None

    base_idx = min(max(0, base_frame_index), len(dkm_data.frames) - 1)
    base_frame = dkm_data.frames[base_idx]

    scale = base_frame.scale
    translate = base_frame.translate

    base_vertices = []
    for v in base_frame.vertices:
        x = v.vertex[0] * scale[0] + translate[0]
        y = v.vertex[1] * scale[1] + translate[1]
        z = v.vertex[2] * scale[2] + translate[2]
        base_vertices.append((x, y, z))

    faces = []
    uv_loops = []
    poly_material_indices = []
    face_surface_indices = [] #per face, for the dkm_surface attribute

    surface_vertex_maps = {i: set() for i in range(len(dkm_data.surfaces))}
    if UVsFromGLCommands:
        for glc in dkm_data.GLCommands:
            count = glc.count
            if count == 0:
                continue

            surf_idx = glc.surfIndex
            skin_idx = glc.skinIndex
            if dkm_data.surfaces and 0 <= surf_idx < len(dkm_data.surfaces):
                skin_idx = dkm_data.surfaces[surf_idx].skinIndex

            mat_idx = skin_idx if 0 <= skin_idx < (len(dkm_data.skinpaths) if dkm_data.skinpaths else 1) else 0
            verts = glc.vertices
            num_verts = len(verts)

            if num_verts < 3:
                continue

            #positive for strip, negative for fan
            if count < 0:
                #Fan
                hub = verts[0]
                if surf_idx in surface_vertex_maps:
                    surface_vertex_maps[surf_idx].add(hub.vertexIndex)

                for i in range(1, num_verts - 1):
                    v1 = verts[i]
                    v2 = verts[i + 1]

                    faces.append((hub.vertexIndex, v2.vertexIndex, v1.vertexIndex)) #reversed winding to fix normal flipping
                    uv_loops.append((hub.s, 1.0 - hub.t))
                    uv_loops.append((v2.s, 1.0 - v2.t))
                    uv_loops.append((v1.s, 1.0 - v1.t))
                    poly_material_indices.append(mat_idx)
                    face_surface_indices.append(surf_idx)

                    if surf_idx in surface_vertex_maps:
                        surface_vertex_maps[surf_idx].add(v1.vertexIndex)
                        surface_vertex_maps[surf_idx].add(v2.vertexIndex)
            else:
                #Strip
                for i in range(num_verts - 2):
                    v0 = verts[i]
                    v1 = verts[i + 1]
                    v2 = verts[i + 2]

                    if i % 2 == 0:
                        faces.append((v0.vertexIndex, v2.vertexIndex, v1.vertexIndex)) #reversed winding to fix normal flipping
                        uv_loops.append((v0.s, 1.0 - v0.t))
                        uv_loops.append((v2.s, 1.0 - v2.t))
                        uv_loops.append((v1.s, 1.0 - v1.t))
                    else:
                        faces.append((v0.vertexIndex, v1.vertexIndex, v2.vertexIndex)) #this one has non-reversed winding though
                        uv_loops.append((v0.s, 1.0 - v0.t))
                        uv_loops.append((v1.s, 1.0 - v1.t))
                        uv_loops.append((v2.s, 1.0 - v2.t))

                    poly_material_indices.append(mat_idx)
                    face_surface_indices.append(surf_idx)

                    if surf_idx in surface_vertex_maps:
                        surface_vertex_maps[surf_idx].add(v0.vertexIndex)
                        surface_vertex_maps[surf_idx].add(v1.vertexIndex)
                        surface_vertex_maps[surf_idx].add(v2.vertexIndex)

    else:
        for tri in dkm_data.triangles:
            faces.append((tri.vertexIndices[0], tri.vertexIndices[2], tri.vertexIndices[1])) #reversed winding to fix normal flipping
            face_surface_indices.append(tri.surfaceIndex)

            if tri.surfaceIndex in surface_vertex_maps:
                for v_idx in tri.vertexIndices:
                    surface_vertex_maps[tri.surfaceIndex].add(v_idx)

            surf = dkm_data.surfaces[tri.surfaceIndex] if dkm_data.surfaces and 0 <= tri.surfaceIndex < len(
                dkm_data.surfaces) else None
            s_width = surf.skinWidth if surf and surf.skinWidth > 0 else 256
            s_height = surf.skinHeight if surf and surf.skinHeight > 0 else 256

            for tex_idx in (tri.textureIndices[0], tri.textureIndices[2], tri.textureIndices[1]): #reversed winding to fix normal flipping
                if tex_idx < len(dkm_data.UV):
                    uv_coord = dkm_data.UV[tex_idx]
                    u = uv_coord.s / s_width
                    v = 1.0 - (uv_coord.t / s_height)
                    uv_loops.append((u, v))
                else:
                    uv_loops.append((0.0, 0.0))

    mesh_name = filepath.stem
    mesh = bpy.data.meshes.new(mesh_name)
    obj = bpy.data.objects.new(mesh_name, mesh)

    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    mesh.from_pydata(base_vertices, [], faces)
    mesh.update()

    #Materials
    for path_str in dkm_data.skinpaths or []:
        obj.data.materials.append(_get_or_create_material(path_str, filepath, ImportTextures))

    if UVsFromGLCommands:
        for poly_i, mat_idx in enumerate(poly_material_indices):
            if poly_i < len(mesh.polygons) and 0 <= mat_idx < len(obj.data.materials):
                mesh.polygons[poly_i].material_index = mat_idx
    else:
        for tri_i, tri in enumerate(dkm_data.triangles):
            if tri_i < len(mesh.polygons):
                if dkm_data.surfaces and 0 <= tri.surfaceIndex < len(dkm_data.surfaces):
                    skin_idx = dkm_data.surfaces[tri.surfaceIndex].skinIndex
                    if 0 <= skin_idx < len(obj.data.materials):
                        mesh.polygons[tri_i].material_index = skin_idx

    #Below is a debug feature: faces get a surface index attribute that can be viewed in the GeoNodes tab. This is
    #helpful when a vertex belongs to multiple surfaces/vertex groups. The model version and origin are stored the
    #same way (the same value on every face), since attributes are what the spreadsheet shows.
    n_faces = len(mesh.polygons)
    if n_faces:
        if len(face_surface_indices) == n_faces:
            attr = mesh.attributes.new(name="dkm_surface", type='INT', domain='FACE')
            attr.data.foreach_set("value", face_surface_indices)
        header = getattr(dkm_data, "header", None)
        if header is not None:
            attr = mesh.attributes.new(name="dkm_version", type='INT', domain='FACE')
            attr.data.foreach_set("value", [int(header.version)] * n_faces)
            attr = mesh.attributes.new(name="dkm_origin", type='FLOAT_VECTOR', domain='FACE')
            attr.data.foreach_set("vector", [float(c) for c in header.origin[:3]] * n_faces)

    if dkm_data.surfaces:
        for surf_idx, surf in enumerate(dkm_data.surfaces):
            vg_name = f"DKM__{surf.name}__{surf.skinIndex}.{surf.flags}"
            vg = obj.vertex_groups.new(name=vg_name)
            vert_indices = list(surface_vertex_maps.get(surf_idx, []))
            if vert_indices:
                vg.add(vert_indices, 1.0, 'REPLACE')

    uv_layer = mesh.uv_layers.new(name="DKM_UVMap")
    uv_layer_data = uv_layer.data
    if uv_loops:
        for i, loop in enumerate(mesh.loops):
            if i < len(uv_loops):
                uv_layer_data[i].uv = uv_loops[i]

    if ImportAnimations:
        _import_animation(obj, dkm_data, TimelineMarkerOptions)
    print(f"Successfully imported '{mesh_name}'.")
    return obj

def _import_animation(obj, dkm_data, marker_mode: int):
    basis_key = obj.shape_key_add(name="Basis")
    basis_key.value = 0.0
    scene = bpy.context.scene
    scene.frame_start = 0

    for f_idx, frame in enumerate(dkm_data.frames):
        key_name = frame.name if frame.name else f"Frame_{f_idx:03d}"
        s_key = obj.shape_key_add(name=key_name, from_mix=False)
        s_key.relative_key = basis_key
        s_key.value = 0.0

        f_scale = frame.scale
        f_translate = frame.translate
        for v_i, v in enumerate(frame.vertices):
            s_key.data[v_i].co = (
                v.vertex[0] * f_scale[0] + f_translate[0],
                v.vertex[1] * f_scale[1] + f_translate[1],
                v.vertex[2] * f_scale[2] + f_translate[2])

        s_key.value = 0.0
        s_key.keyframe_insert(data_path="value", frame=max(0, f_idx - 1))
        s_key.value = 1.0
        s_key.keyframe_insert(data_path="value", frame=f_idx)
        s_key.value = 0.0
        s_key.keyframe_insert(data_path="value", frame=f_idx + 1)

    scene.frame_end = len(dkm_data.frames) - 1

    if marker_mode == 1:
        mark = scene.timeline_markers
        mark.clear()
        last_name = ""
        for f_idx, frame in enumerate(dkm_data.frames):
            raw = frame.name if frame.name else f"frame_{f_idx}"
            base = raw.rstrip("0123456789_")
            if base and base != last_name:
                mark.new(name=base, frame=f_idx)
                last_name = base
    elif marker_mode == 2:
        mark = scene.timeline_markers
        mark.clear()
        for seq in dkm_data.sequences:
            if seq.name:
                mark.new(name=seq.name, frame=seq.startFrame)

    obj.active_shape_key_index = 0