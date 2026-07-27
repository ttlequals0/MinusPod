# Assets Directory

`replace.mp3` here is the default sound played where an advertisement was
removed. Most people should change it from the web UI rather than editing this
directory. Settings > Audio > Replacement audio plays the current sound, takes
an upload, and restores the default. Uploads are stored on the data volume, so
they survive a container rebuild, and they take precedence over this file.

Editing this directory still works, and is the way to bake your own default
into an image. The file must be named exactly `replace.mp3`.

## Channel layout

Use a stereo (2-channel) file. MinusPod keeps each podcast's own channel layout
in the output, except when a replacement lands at the very start of an episode
(a pre-roll ad): there the spliced output takes the replacement's channel count
for that episode. So a mono `replace.mp3` can downmix a stereo show to mono,
while a stereo file is always safe (a mono show is adapted with no quality loss).
The bundled default is already stereo; this only matters if you supply your own.

## Length

Every cut becomes as long as this file, so a long replacement pads out every ad
break in every episode. The bundled default is about one second. Uploads through
the UI are capped at 30 seconds and 5 MB.
