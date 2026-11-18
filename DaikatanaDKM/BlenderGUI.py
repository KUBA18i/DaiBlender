import os
import bpy
from bpy.props import StringProperty, IntProperty, BoolProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
from pathlib import Path

from .BlenderImport import import_dkm_file
from .BlenderExport import export_dkm_file

DK_icon = None

class DKM_OT_import(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.dkm"
    bl_label = "Import DKM"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".dkm"
    filter_glob: StringProperty(
        default="*.dkm",
        options={'HIDDEN'},
        maxlen=255,
    )

    BaseFrame: IntProperty(
        name="Base frame",
        description="When importing animations, this frame will be the primary frame, upon which all the shape keys will be based on.\nIf no animations are imported, this frame will be imported as a static mesh.",
        default=0,
        min=0,
        soft_min=0
    )

    UVsFromGLCommands: BoolProperty(
        name="Fetch UV data from GL Commands",
        description="If unchecked, the model's UV and Triangle structs will be used to fetch the UV data. Keep in mind, they are unused in-game, since Daikatana lacks a software renderer.",
        default=False
    )

    ImportTextures: BoolProperty(
        name="Import textures",
        description="If checked, the importer will try to find files specified in the skin paths."
        "\nFirst, they will be searched in the same folder as the model, and if that fails,"
        "\nskin paths will be used to find the actual path. Files will be searched in this order:"
        "\n.wal -> .pcx -> .tga -> .bmp -> .png",
        default=True
    )

    ImportAnimations: BoolProperty(
        name="Import animations",
        description="If checked, animation frames will be imported as relative Shape Keys.",
        default=True
    )

    TimelineMarkerOptions: bpy.props.EnumProperty(
        name="Timeline Markers",
        description="Importing timeline markers results in the current markers being deleted.",
        items=[
            ('0', "No markers",
             "No timeline markers will be imported and the current ones will be intact."),
            ('1', "Markers from Frame names",
             "Timeline Markers will be fetched from the frame names, with their numbers stripped."),
            ('2', "Markers from Animations Sequences",
             "Timeline Markers will be fetched from Animation Sequences. If the model has no AnimSeq data block, the timeline will be flushed.")
        ],
        default='1'
    )

    def draw(self, context):
        layout = self.layout

        box1 = layout.box()
        box1.label(text="Basic settings", icon="OBJECT_DATA")
        box1.prop(self, "BaseFrame")
        box1.prop(self, "UVsFromGLCommands")
        box1.prop(self, "ImportTextures")

        box2 = layout.box()
        box2.label(text="Animation", icon="ANIM_DATA")
        row1 = box2.column_flow(columns=1, align=True)
        row1.prop(self, "ImportAnimations")
        row2 = box2.column_flow(columns=1, align=True)
        row2.enabled = self.ImportAnimations
        row2.prop(self, "TimelineMarkerOptions")

    def execute(self, context):
        try:
            import_dkm_file(self.filepath, self.BaseFrame, self.UVsFromGLCommands, self.ImportAnimations, int(self.TimelineMarkerOptions), self.ImportTextures)
            self.report({'INFO'}, f"Successfully imported {os.path.basename(self.filepath)}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to import DKM: {str(e)}")
            return {'CANCELLED'}
        return {'FINISHED'}

def LimitFrameRange(self, context):
    scene = context.scene
    if self.StartFrame < scene.frame_start:
        self.StartFrame = scene.frame_start
    elif self.StartFrame > scene.frame_end:
        self.StartFrame = scene.frame_end
    if self.EndFrame < scene.frame_start:
        self.EndFrame = scene.frame_start
    elif self.EndFrame > scene.frame_end:
        self.EndFrame = scene.frame_end

class DKM_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.dkm"
    bl_label = "Export DKM"
    bl_options = {'REGISTER'}

    ModelVersion: bpy.props.EnumProperty(
        name="Model Version",
        description="Version 1 models use 8-bit vertex coordinates, whereas Version 2 packs them into a single unsigned 32-bit integer, split into 11-10-11 bits per coordinate.\n\nUse V1 for smaller filesize, or V2 for higher precision.",
        items=[
            ('1', "Version 1", "8-bit vertices"),
            ('2', "Version 2", "11/10/11-bit vertices"),
        ],
        default='1'
    )

    Origin: bpy.props.FloatVectorProperty(
        name="Model origin",
        size=3,
        subtype='XYZ'
    )

    SurfacesFromVertexGroups: BoolProperty(
        name="Create surfaces from vertex groups.",
        description="If checked, vertex groups will be used to create surfaces, if named in the specified format. If unchecked, a single default surface will be made, pointing to the first skin.\nThe name format is: DKM__<Surface name>__<Skin number>.<Flag value>",
        default = True
    )

    ExportTextures: BoolProperty(
        name="Export textures",
        description="If checked, material textures will be exported in the same folder as the model, in the format specified by the skin path.",
        default = True
    )

    ExportAnimations: BoolProperty(
        name="Export animations",
        description="Export all types of animations of the selected objects between set points in the timeline.",
        default = True
    )

    StartFrame: IntProperty(
        name="Start animation frame",
        default=0,
        min=0,
        soft_min=0,
        update=LimitFrameRange
    )

    EndFrame: IntProperty(
        name="End animation frame",
        default=2048,
        min=0,
        soft_min=0,
        update=LimitFrameRange
    )

    FramesFromMarkers: BoolProperty(
        name="Fetch frame names from Timeline Markers",
        description="If unchecked, the frames will have a generic name \"frame\" with a number appended to it",
        default = True
    )

    AnimSeqOptions: bpy.props.EnumProperty(
        name="AnimSeq",
        description="Select if and how the Animations Sequence data block should be populated.",
        items=[
            ('0', "No AnimSeq data block", "The model will lack animation sequences and thus the header will be 8 bytes shorter."),
            ('1', "One unified sequence", "The model will have an AnimSeq block containing a single sequence from beginning to end, named \"Default\"."),
            ('2', "Sequences from Timeline Markers", "Timeline Markers will be used to create sequences.\nIf the start frame precedes any markers, the first sequence will be called \"Default\".")
        ],
        default='2'
    )

    filename_ext = ".dkm"
    filter_glob: StringProperty(
        default="*.dkm",
        options={'HIDDEN'},
        maxlen=255,
    )

    def invoke(self, context, event):
        self.StartFrame = context.scene.frame_start
        self.EndFrame = context.scene.frame_end
        return super().invoke(context, event)

    def draw(self, context):
        layout = self.layout

        box1 = layout.box()
        box1.label(text="Model Properties", icon="OBJECT_DATA")
        box1.prop(self, "ModelVersion")
        box1.prop(self, "Origin")
        box1.prop(self, "ExportTextures")
        box1.prop(self, "SurfacesFromVertexGroups")

        box2 = layout.box()
        box2.label(text="Animation", icon="ANIM_DATA")
        row1 = box2.column_flow(columns=1, align=True)
        row1.prop(self, "ExportAnimations")
        row2 = box2.column_flow(columns=1, align=True)
        row2.enabled = self.ExportAnimations
        row2.prop(self, "StartFrame")
        row2.prop(self, "EndFrame")
        row2.prop(self, "FramesFromMarkers")
        row2.prop(self, "AnimSeqOptions")

    def execute(self, context):
        try:
            objs = sorted((o for o in context.selected_objects if o.type == 'MESH'), key=lambda o: o.name)
            if not objs and context.active_object and context.active_object.type == 'MESH':
                objs = [context.active_object]
            if not objs:
                self.report({'ERROR'}, "Select at least one mesh object to export")
                return {'CANCELLED'}
            export_dkm_file(self.filepath, objs, int(self.ModelVersion), list(self.Origin), self.SurfacesFromVertexGroups, self.ExportAnimations, self.FramesFromMarkers, self.StartFrame, self.EndFrame, int(self.AnimSeqOptions), self.ExportTextures)
            self.report({'INFO'}, f"Successfully exported {os.path.basename(self.filepath)}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to export DKM: {str(e)}")
            return {'CANCELLED'}
        return {'FINISHED'}


def menu_func_import(self, layout):
    icon_id = DK_icon["dkm_icon"].icon_id if DK_icon else 0
    self.layout.operator(DKM_OT_import.bl_idname, text="Daikatana model (.dkm)", icon_value=icon_id)

def menu_func_export(self, layout):
    icon_id = DK_icon["dkm_icon"].icon_id if DK_icon else 0
    self.layout.operator(DKM_OT_export.bl_idname, text="Daikatana model (.dkm)", icon_value=icon_id)


def register_icon():
    global DK_icon
    import bpy.utils.previews
    DK_icon = bpy.utils.previews.new()
    icon_path = Path(__file__).parent / "icon.svg"

    if icon_path.exists():
        DK_icon.load("dkm_icon", str(icon_path), 'IMAGE')
    else:
        print(f"Warning: Icon file not found at {icon_path}")


def unregister_icon():
    global DK_icon
    if DK_icon is not None:
        bpy.utils.previews.remove(DK_icon)
        DK_icon = None


classes = (
    DKM_OT_import,
    DKM_OT_export,
)