# SriSu demo photo pack

These bundled images support the fictional local demo dataset. They are source
fixtures, not user uploads. Runtime seeding is offline. `manifest.json` records
each file's source, SHA-256, and licence; filenames are stable.

The 100 `person_*.webp` images come from [Open Pravatar](https://openpravatar.com/).
Its [usage licence](https://openpravatar.com/legal), checked 2026-11-22, allows
synthetic images in commercial/non-commercial software UIs and demos without
attribution. It prohibits selling/redistributing the images as stock photography
or a competing asset library. This pack is an application demo fixture, not an
asset library product. The portraits depict AI-generated people. Names, dates,
locations, and relationships attached by SriSu are fictional and unrelated to
the catalogue's titles or locations.

The six `scene_*.jpg` images come from [Lorem Picsum](https://picsum.photos/),
using Unsplash photos under the [Unsplash licence](https://unsplash.com/license).
The manifest includes the original Unsplash link and photographer credit for
each. Scenery is reused across albums, covers, and moments; it does not purport
to document the fictional account holders' real lives.

No image generation service or photo host is called by the command. Keep this
directory with the application source when copying it to another development
machine. Generated media copies go under ignored `media/**/demo_v1/` paths.
