import bpy, re, struct, bmesh
import numpy as np
from pathlib import Path
from collections.abc import Sequence
from .DKM_IO import read_dkm, write_dkm
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

def import_dkm_file(filepath: str | Path, base_frame_index: int, UVsFromGLCommands: bool, ImportAnimations: bool, TimelineMarkerOptions: int):
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

                    if surf_idx in surface_vertex_maps:
                        surface_vertex_maps[surf_idx].add(v0.vertexIndex)
                        surface_vertex_maps[surf_idx].add(v1.vertexIndex)
                        surface_vertex_maps[surf_idx].add(v2.vertexIndex)

    else:
        for tri in dkm_data.triangles:
            faces.append((tri.vertexIndices[0], tri.vertexIndices[2], tri.vertexIndices[1])) #reversed winding to fix normal flipping

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

    if dkm_data.skinpaths:
        for path_str in dkm_data.skinpaths:
            mat_name = path_str if path_str else "DKM_Material"
            mat = bpy.data.materials.get(mat_name)
            if not mat:
                mat = bpy.data.materials.new(name=mat_name)
                mat.use_nodes = True
                mat_nodes = mat.node_tree.nodes
                mat_links = mat.node_tree.links

                while mat_nodes:
                    mat_nodes.remove(mat_nodes[0])

                node_diff = mat_nodes.new(type='ShaderNodeBsdfDiffuse')
                node_out_mat = mat_nodes.new(type='ShaderNodeOutputMaterial')
                node_out_mat.location = (200, 0)
                mat_links.new(node_diff.outputs['BSDF'], node_out_mat.inputs['Surface'])

            obj.data.materials.append(mat)
    else:
        mat = bpy.data.materials.new(name="Default_DKM_Mat")
        mat.use_nodes = True
        mat_nodes = mat.node_tree.nodes
        mat_links = mat.node_tree.links
        while mat_nodes:
            mat_nodes.remove(mat_nodes[0])
        node_diff = mat_nodes.new(type='ShaderNodeBsdfDiffuse')
        node_out_mat = mat_nodes.new(type='ShaderNodeOutputMaterial')
        mat_links.new(node_diff.outputs['BSDF'], node_out_mat.inputs['Surface'])
        obj.data.materials.append(mat)

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


def _material_skin_size(mat) -> tuple[int, int]:
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
    if img is not None:
        w, h = img.size
        if w > 0 and h > 0:
            return int(w), int(h)
        print(f"Warning: image '{img.name}' has no pixel data; assuming 256x256.")
    return 256, 256


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


def export_dkm_file(filepath, objs, ModelVersion: int, origin: list[float], SurfacesFromVertexGroups: bool, ExportAnimations: bool, MarkerFrameNames: bool, StartAnimFrame: int, EndAnimFrame: int, AnimSeqOptions: int):
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

        for obj, mesh in zip(objs, base_meshes):
            def global_skin(mat):
                name = mat.name if mat else "default_skin.tga"
                if name not in skinpaths:
                    skinpaths.append(name)
                    skin_sizes.append(_material_skin_size(mat))
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
                dkm.triangles.append(DKMTriangle(
                    surfaceIndex=surf_global[assigned],
                    num_uvframes=1,
                    vertexIndices=[v[0], v[2], v[1]],        # reversed winding
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

        frames = []
        for name, co in zip(names, frame_coords):
            mn = co.min(axis=0)
            rng = co.max(axis=0) - mn
            scale = np.where(rng > 0, rng / qmax, 1.0)
            q = np.clip(np.rint((co - mn) / scale), 0, qmax).astype(int)
            frame_verts = [DKMTriangleVertex(vertex=[int(a), int(b), int(c)], lightNormalIndex=0)
                           for a, b, c in q]
            frames.append(DKMFrame(scale=[float(s) for s in scale],
                                   translate=[float(t) for t in mn],
                                   name=name, vertices=frame_verts, unknown=[0, 0, 0]))
        dkm.frames = frames
        dkm.sequences = _build_sequences(len(frames), AnimSeqOptions, StartAnimFrame, EndAnimFrame)

        write_dkm(dkm, filepath)
        print(f"Successfully exported {len(objs)} object(s) to '{filepath.name}'.")
        return True

    finally:
        for m in temp_meshes:
            bpy.data.meshes.remove(m)

