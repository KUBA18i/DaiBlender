# DaiBlender
Blender add-on for import/export of Daikatana models (.dkm)

# Examples
1.An example of Hiro's model being importe into the Blender scene, with surfaces turned into vertex groups.  
![Hiro's model inside Blender](https://i.imgur.com/isvh22L.png)  
2. A series of animated cubes. Together they are exported as one model, with each cube having its own surface, skin and flag effect. Open links to see videos:  
[How it looks in Blender](https://imgur.com/ZRicYam)  
[In-game footage](https://imgur.com/c7RNdKT)  

# Usage Manual
## Import
![Import DKM menu](https://i.imgur.com/eUQGNGH.png)  
* The importer works with both Version 1 and 2 .dkm models.  
* You're given the option to import UVs from either the Triangle/UV structs, or from the GL Commands block. Only the latter are used in-game, since DK lacks a software renderer.  
* You can either import a static mesh, or animated with shape keys.  
* If importing a static mesh, the base frame will be the only one that's imported. Otherwise, all shape keys will based on it.  
* Skins are not loaded automatically, you will need to find and load them yourself. You will likely need to export and convert them. For that I recommend SLADE/DaiPAK and DaiWAL,   respectively.  
* If importing animations, you have the option to populate the timeline with markers, depending on the frame or AnimSeq names.  
* Model surfaces will be imported as vertex groups, with a naming scheme of `DKM__<Surface name>__<Skin name>.<Flag value>`  
* Model Origin is not kept after import, but it is printed in the Blender console.  

## Export
![Export DKM menu](https://i.imgur.com/aHBVRfh.png)  
* You can select multiple objects to be exported as a single model. Their total material and surface lists will be fused after the objects are sorted in alphabetic order.  
* Material names will be used as path names that the game looks for to find textures, so make sure they point to valid files (.wal or .tga).  
* You can export the model in either version 1 (8-bit vertices) or 2 (11/10/11-bit vertices packed into a single uint64_t). Pick former for smaller file size, or the latter for more accuracy.  
* You can set the model origin by hand.  
* If the corresponding option is checked, the objects' vertex groups will be used to create surfaces, using the naming scheme of `DKM__<Surface name>__<Skin name>.<Flag value>`. Otherwise, a single default surface will be made.  
* You can either export a static mesh, or an animation range. The export will fail if the vertex count changes throughout the timeline.  
* You can create frame names from timeline markers, or set to default values of `frame#`.  
* You have the option to create an Animation Sequence data block from timeline markers, a single unified one, or omit one.  

# Installation
You can install this add-on like any other: grab the zip from the releases, or download the repo. In Blender, select `Edit > Preferences > Add-ons > Install` and pick the zip you just downloaded. Then you can enable it in the add-on list.

# Credits
This add-on was made by Jakub Majewski.  
Daikatana and its model format is by Ion Storm.  
Blender is by the Blender Foundation.
I used the Kingpin MD2/MDX addon by Hypov8 as reference; their GL Commands builder was very useful to me.
Special thanks to the DK Beta 1.3 patch team for assistance with the model format.  
