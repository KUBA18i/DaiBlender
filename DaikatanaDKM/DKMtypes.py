from dataclasses import dataclass, field

@dataclass
class DKMHeader:
    ident: int = 0
    version: int = 0
    origin: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    frameSize: int = 0
    numSkins: int = 0
    numVertices: int = 0
    numTexCoords: int = 0
    numTriangles: int = 0
    numGlCommands: int = 0
    numFrames: int = 0
    numSurfaces: int = 0
    offsetSkins: int = 0
    offsetTexCoords: int = 0
    offsetTriangles: int = 0
    offsetFrames: int = 0
    offsetGlCommands: int = 0
    offsetSurfaces: int = 0
    offsetEnd: int = 0
    numSequences: int = 0
    offsetSequences: int = 0

@dataclass
class DKMTextureCoordinate:
    s: int = 0
    t: int = 0

@dataclass
class DKMTriangle:
    surfaceIndex: int = 0
    num_uvframes: int = 0
    vertexIndices: list = field(default_factory=lambda: [0, 0, 0])
    textureIndices: list = field(default_factory=lambda: [0, 0, 0])

@dataclass
class DKMTriangleVertex:
    vertex: list = field(default_factory=lambda: [0, 0, 0])
    lightNormalIndex: int = 0

@dataclass
class DKMFrame:
    scale: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    translate: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    name: str = ""
    vertices: list = field(default_factory=list)
    unknown: list = field(default_factory=lambda: [0, 0, 0])

@dataclass
class DKMGLCommandVertex:
    vertexIndex: int = 0
    s: float = 0.0
    t: float = 0.0

@dataclass
class DKMGLCommand:
    count: int = 0
    skinIndex: int = 0
    surfIndex: int = 0
    vertices: list = field(default_factory=list)

@dataclass
class DKMSurface:
    name: str = ""
    flags: int = 0
    skinIndex: int = 0
    skinWidth: int = 0
    skinHeight: int = 0
    numUVframes: int = 0

@dataclass
class DKMAnimSeq:
    name: str = ""
    startFrame: int = 0
    endFrame: int = 0

@dataclass
class DKMFile:
    header: DKMHeader = field(default_factory=DKMHeader)
    skinpaths: list = field(default_factory=list)
    UV: list = field(default_factory=list)
    triangles: list = field(default_factory=list)
    frames: list = field(default_factory=list)
    GLCommands: list = field(default_factory=list)
    surfaces: list = field(default_factory=list)
    sequences: list = field(default_factory=list)

