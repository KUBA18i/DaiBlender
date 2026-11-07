from pathlib import Path
import struct
from .DKMtypes import (
    DKMFile,
    DKMTextureCoordinate,
    DKMTriangle,
    DKMTriangleVertex,
    DKMFrame,
    DKMGLCommandVertex,
    DKMGLCommand,
    DKMSurface,
    DKMAnimSeq,
)

def read_dkm(inpath: str | Path) -> DKMFile:
    inpath = Path(inpath)
    print(f"Loading Daikatana model: \"{inpath}\"")
    new_dkm = DKMFile()

    with open(inpath, "rb") as f:
        header_data = struct.unpack("2i3f15i", f.read(80))
        new_dkm.header.ident = header_data[0]
        new_dkm.header.version = header_data[1]
        print(f"Model is version {new_dkm.header.version}.")
        new_dkm.header.origin = [header_data[2], header_data[3], header_data[4]]
        print(f"Model's origin: {new_dkm.header.origin}")
        new_dkm.header.frameSize = header_data[5]
        new_dkm.header.numSkins = header_data[6]
        new_dkm.header.numVertices = header_data[7]
        new_dkm.header.numTexCoords = header_data[8]
        new_dkm.header.numTriangles = header_data[9]
        new_dkm.header.numGlCommands = header_data[10]
        new_dkm.header.numFrames = header_data[11]
        new_dkm.header.numSurfaces = header_data[12]
        new_dkm.header.offsetSkins = header_data[13]
        new_dkm.header.offsetTexCoords = header_data[14]
        new_dkm.header.offsetTriangles = header_data[15]
        new_dkm.header.offsetFrames = header_data[16]
        new_dkm.header.offsetGlCommands = header_data[17]
        new_dkm.header.offsetSurfaces = header_data[18]
        new_dkm.header.offsetEnd = header_data[19]

        if new_dkm.header.offsetSkins == 80:
            print("Animation sequence data block is absent.")
            new_dkm.header.numSequences = 0
            new_dkm.header.offsetSequences = new_dkm.header.offsetEnd
        elif new_dkm.header.offsetSkins == 88:
            print("Animation sequence data block is present.")
            new_dkm.header.numSequences = struct.unpack("i", f.read(4))[0]
            new_dkm.header.offsetSequences = struct.unpack("i", f.read(4))[0]
        else:
            raise ValueError(
                f"Error: Strange number encountered for skin offset:"
                f" {new_dkm.header.offsetSkins}. The DKM file is likely corrupted."
            )

        f.seek(new_dkm.header.offsetSkins)
        for _ in range(new_dkm.header.numSkins):
            skin_path = f.read(64).split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
            new_dkm.skinpaths.append(skin_path)

        f.seek(new_dkm.header.offsetTexCoords)
        for _ in range(new_dkm.header.numTexCoords):
            s, t = struct.unpack("hh", f.read(4))
            new_dkm.UV.append(DKMTextureCoordinate(s=s, t=t))

        f.seek(new_dkm.header.offsetTriangles)
        for _ in range(new_dkm.header.numTriangles):
            data = struct.unpack("8h", f.read(16))
            tri = DKMTriangle(
                surfaceIndex=data[0],
                num_uvframes=data[1],
                vertexIndices=[data[2], data[3], data[4]],
                textureIndices=[data[5], data[6], data[7]],
            )
            new_dkm.triangles.append(tri)

        f.seek(new_dkm.header.offsetFrames)
        for _ in range(new_dkm.header.numFrames):
            scale = struct.unpack("3f", f.read(12))
            translate = struct.unpack("3f", f.read(12))
            name = f.read(16).split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()

            vertices = []
            for _ in range(new_dkm.header.numVertices):
                if new_dkm.header.version == 1:
                    vx, vy, vz = struct.unpack("3B", f.read(3))
                    light_normal = struct.unpack("B", f.read(1))[0]
                    vert = DKMTriangleVertex(
                        vertex=[vx, vy, vz], lightNormalIndex=light_normal
                    )
                elif new_dkm.header.version == 2:
                    packvert = struct.unpack("I", f.read(4))[0]
                    vx = (packvert & 0xFFE00000) >> 21
                    vy = (packvert & 0x1FF800) >> 11
                    vz = packvert & 0x7FF
                    light_normal = struct.unpack("B", f.read(1))[0]
                    vert = DKMTriangleVertex(
                        vertex=[vx, vy, vz], lightNormalIndex=light_normal
                    )
                vertices.append(vert)

            if new_dkm.header.version == 2:
                unknown = list(struct.unpack("3B", f.read(3)))
            else:
                unknown = [0, 0, 0]

            frame = DKMFrame(
                scale=list(scale),
                translate=list(translate),
                name=name,
                vertices=vertices,
                unknown=unknown,
            )
            new_dkm.frames.append(frame)

        f.seek(new_dkm.header.offsetGlCommands)
        while True:
            cmd_bytes = f.read(4)
            if not cmd_bytes:
                break
            command = struct.unpack("i", cmd_bytes)[0]
            if command == 0:
                break

            num_verts = abs(command)
            skin_index = struct.unpack("i", f.read(4))[0]
            surf_index = struct.unpack("i", f.read(4))[0]

            cmd_verts = []
            for _ in range(num_verts):
                v_idx, v_s, v_t = struct.unpack("iff", f.read(12))
                cmd_verts.append(DKMGLCommandVertex(vertexIndex=v_idx, s=v_s, t=v_t))

            gl_cmd = DKMGLCommand(
                count=command,
                skinIndex=skin_index,
                surfIndex=surf_index,
                vertices=cmd_verts,
            )
            new_dkm.GLCommands.append(gl_cmd)

        f.seek(new_dkm.header.offsetSurfaces)
        for _ in range(new_dkm.header.numSurfaces):
            surf_name = f.read(32).split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
            flags, skin_idx, skin_w, skin_h, num_uv = struct.unpack("5i", f.read(20))
            surface = DKMSurface(
                name=surf_name,
                flags=flags,
                skinIndex=skin_idx,
                skinWidth=skin_w,
                skinHeight=skin_h,
                numUVframes=num_uv,
            )
            new_dkm.surfaces.append(surface)

        if new_dkm.header.numSequences > 0:
            f.seek(new_dkm.header.offsetSequences)
            for _ in range(new_dkm.header.numSequences):
                seq_name = f.read(16).split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
                start_f, end_f = struct.unpack("2i", f.read(8))
                seq = DKMAnimSeq(name=seq_name, startFrame=start_f, endFrame=end_f)
                new_dkm.sequences.append(seq)

    return new_dkm

def write_dkm(dkm: DKMFile, outpath: str | Path):
  outpath = Path(outpath)

  with open(outpath, "wb") as f:
    has_sequences = len(dkm.sequences) > 0
    header_size = 88 if has_sequences else 80
    current_offset = header_size

    dkm.header.offsetSkins = current_offset
    current_offset += len(dkm.skinpaths) * 64

    dkm.header.offsetTexCoords = current_offset
    current_offset += len(dkm.UV) * 4

    dkm.header.offsetTriangles = current_offset
    current_offset += len(dkm.triangles) * 16

    dkm.header.offsetFrames = current_offset
    for frame in dkm.frames:
      frame_bytes = 40
      vert_size = 4 if dkm.header.version == 1 else 5
      frame_bytes += len(frame.vertices) * vert_size
      if dkm.header.version == 2:
        frame_bytes += 3
      current_offset += frame_bytes

    dkm.header.offsetGlCommands = current_offset
    for cmd in dkm.GLCommands:
      current_offset += 12 + len(cmd.vertices) * 12
    current_offset += 4

    dkm.header.offsetSurfaces = current_offset
    current_offset += len(dkm.surfaces) * 52

    if has_sequences:
      dkm.header.offsetSequences = current_offset
      current_offset += len(dkm.sequences) * 24

    dkm.header.offsetEnd = current_offset

    dkm.header.numSkins = len(dkm.skinpaths)
    dkm.header.numTexCoords = len(dkm.UV)
    dkm.header.numTriangles = len(dkm.triangles)
    dkm.header.numFrames = len(dkm.frames)

    vert_size = 4 if dkm.header.version == 1 else 5
    single_frame_bytes = 40 + (len(dkm.skinpaths) * 0)
    dkm.header.frameSize = 40 + len(dkm.frames[0].vertices) * vert_size
    if dkm.header.version == 2:
        dkm.header.frameSize += 3

    num_gl_cmds = 0
    for cmd in dkm.GLCommands:
      num_gl_cmds += 3
      num_gl_cmds += len(cmd.vertices) * 3
    num_gl_cmds += 1
    dkm.header.numGlCommands = num_gl_cmds

    dkm.header.numSurfaces = len(dkm.surfaces)
    if has_sequences:
      dkm.header.numSequences = len(dkm.sequences)
    else:
      dkm.header.numSequences = 0

    if has_sequences:
      header_format = "2i3f17i"
      header_values = (
          dkm.header.ident,
          dkm.header.version,
          dkm.header.origin[0],
          dkm.header.origin[1],
          dkm.header.origin[2],
          dkm.header.frameSize,
          dkm.header.numSkins,
          dkm.header.numVertices,
          dkm.header.numTexCoords,
          dkm.header.numTriangles,
          dkm.header.numGlCommands,
          dkm.header.numFrames,
          dkm.header.numSurfaces,
          dkm.header.offsetSkins,
          dkm.header.offsetTexCoords,
          dkm.header.offsetTriangles,
          dkm.header.offsetFrames,
          dkm.header.offsetGlCommands,
          dkm.header.offsetSurfaces,
          dkm.header.offsetEnd,
          dkm.header.numSequences,
          dkm.header.offsetSequences,
      )
    else:
      header_format = "2i3f15i"
      header_values = (
          dkm.header.ident,
          dkm.header.version,
          dkm.header.origin[0],
          dkm.header.origin[1],
          dkm.header.origin[2],
          dkm.header.frameSize,
          dkm.header.numSkins,
          dkm.header.numVertices,
          dkm.header.numTexCoords,
          dkm.header.numTriangles,
          dkm.header.numGlCommands,
          dkm.header.numFrames,
          dkm.header.numSurfaces,
          dkm.header.offsetSkins,
          dkm.header.offsetTexCoords,
          dkm.header.offsetTriangles,
          dkm.header.offsetFrames,
          dkm.header.offsetGlCommands,
          dkm.header.offsetSurfaces,
          dkm.header.offsetEnd,
      )

    f.write(struct.pack(header_format, *header_values))

    for skin in dkm.skinpaths:
      f.write(skin.encode("utf-8").ljust(64, b"\x00")[:64])

    for uv in dkm.UV:
      f.write(struct.pack("hh", uv.s, uv.t))

    for tri in dkm.triangles:
      f.write(
          struct.pack(
              "8h",
              tri.surfaceIndex,
              tri.num_uvframes,
              tri.vertexIndices[0],
              tri.vertexIndices[1],
              tri.vertexIndices[2],
              tri.textureIndices[0],
              tri.textureIndices[1],
              tri.textureIndices[2],
          )
      )

    for frame in dkm.frames:
      f.write(struct.pack("3f", *frame.scale))
      f.write(struct.pack("3f", *frame.translate))
      f.write(frame.name.encode("utf-8").ljust(16, b"\x00")[:16])

      for vert in frame.vertices:
        if dkm.header.version == 1:
          f.write(struct.pack("3B", *vert.vertex))
          f.write(struct.pack("B", vert.lightNormalIndex))
        elif dkm.header.version == 2:
          vx, vy, vz = vert.vertex
          packvert = ((vx & 0x7FF) << 21) | ((vy & 0x7FF) << 11) | (vz & 0x7FF)
          f.write(struct.pack("I", packvert))
          f.write(struct.pack("B", vert.lightNormalIndex))

      if dkm.header.version == 2:
        f.write(struct.pack("3B", *frame.unknown))

    for cmd in dkm.GLCommands:
      f.write(struct.pack("i", cmd.count))
      f.write(struct.pack("i", cmd.skinIndex))
      f.write(struct.pack("i", cmd.surfIndex))
      for v in cmd.vertices:
        f.write(struct.pack("iff", v.vertexIndex, v.s, v.t))
    f.write(struct.pack("i", 0))

    for surf in dkm.surfaces:
      f.write(surf.name.encode("utf-8").ljust(32, b"\x00")[:32])
      f.write(
          struct.pack(
              "5i",
              surf.flags,
              surf.skinIndex,
              surf.skinWidth,
              surf.skinHeight,
              surf.numUVframes,
          )
      )

    if has_sequences:
      for seq in dkm.sequences:
        f.write(seq.name.encode("utf-8").ljust(16, b"\x00")[:16])
        f.write(struct.pack("2i", seq.startFrame, seq.endFrame))

