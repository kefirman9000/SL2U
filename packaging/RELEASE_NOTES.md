## Release Kit for Windows

**New install:** download `ReleaseKit-windows-x64.zip`, unzip it anywhere, and double-click `ReleaseKit.exe`. No Python or ffmpeg needed. If Windows says "Windows protected your PC", click **More info → Run anyway** (the app isn't code-signed).

**Already have 1.2?** Unzip this over your old folder once. From 1.3 on, Release Kit updates itself (from 1.3, the update opens a fresh browser tab once).

### New in 1.8.5
- **Snow falls in front of the foreground** (over the rooftops, trees and crowd) while the fireworks stay behind it. In the Fireworks layer, under "Snow falling", you can put the snow behind the foreground again.

### New in 1.8.4
- **Fireworks go on for the whole video.** With a timeline, the first part starts with the song, and after the finale the show carries on to the end instead of stopping. A finale typed in past the end of the song is moved so it still happens.
- **Finale "At the end":** one click puts the finale at the very end of the song, whatever its length. The quick starts now use it.
- **Drag the timeline:** the bar is now the real length of your song, with a time line under it. Drag the white handles to move where a part starts, drag the finale to move it and its edge to change how long it lasts, and double-click to add a part. Click the time line to preview the video from that moment.
- **"How many" can be 0** for a quiet stretch in a show.
- **Smart select** in the foreground maker: click the sky once and the app works out where it ends, along the real edges. Not quite right? Click (or scribble on) what should stay, or more of what should go, and it works it out again. Right-click does the other one. A sky that fades from blue to orange is still found as one sky.
- **Lasso:** draw around an area freehand to remove it, keep only it, or bring it back. It snaps to the real edge nearby, so you can draw loosely.
- **Feather:** choose how many pixels the edge of a selection blends over, and feather the whole edge up to 30 px.
- **Clean edge colours:** soft edges no longer keep a sky-coloured halo.

### New in 1.8.3
- **Rough paint** in the foreground maker: scribble over what you want gone (sky, mountains, stars). You don't have to be careful at the edges: when you let go, the real edge is found from the colours and blended softly. It won't cross a clear edge like a roof line, and dark spots inside a tree stay. **Rough paint: keep** brings parts back the same way.
- The magnetic line's tips now say you can trace whatever should stay in front, for example only the rooftops and the tree, so the mountains go too.

### New in 1.8.2
- **Make a foreground from any photo:** in the Foreground layer, press "Make one from a photo…" to make the sky see-through.
  - **Magnetic line:** click a few points along the top of the buildings, hills or trees. The line sticks to the edges between your clicks. Then remove everything above it, or close it into a shape and remove or keep the inside.
  - **Remove the sky:** a one-click try for clear skies.
  - **Wand:** remove or keep a similar area with one click.
  - **Soft eraser and restore brushes** with a softness slider, and a **Soften edge** brush to blend rough spots.
  - Trim and soften the whole edge, see it against a night sky, checkerboard or red, and undo (Ctrl+Z). Backspace removes the last point of the line.
  - "Touch up the one in use…" reopens your current foreground.
- Snow now also falls in a mixed show or finale that includes Christmas shapes.

### New in 1.8.1
- **Snow is already falling** when the video starts (and rising embers are already in the air), instead of starting at the top.
- **Softer, more natural flashes:** each firework now only lights the sky right around it, more gently and for a shorter moment, instead of brightening the whole screen. Lightning still lights up a wider part of the sky.

### New in 1.8.0
- **Fireworks** (a new layer in the video editor): classic bursts, gold willow, rings, crackle, hearts, a finale, rising embers, lightning and confetti cannons, going off on the beats of your song. Rockets go up early so they burst right on the beat.
- **Christmas shapes:** snowflakes, trees, presents, Santa, candy canes, stars, bells and "Merry Christmas", with a "How exact" slider (sharp picture or loose like a real show) and falling snow.
- **Colours:** every colour, gold, pink and purple, blue and white, red and orange, Christmas, or up to three of your own. Choose how often each type comes up in a mixed show.
- **Plan a show with a timeline:** parts that start at times in your song, each with its own effect and amount, blended into each other, a build-up and a finale at the time you type in.
- **Quick starts:** New Year's show, Christmas show, party confetti.
- **Foreground** (a new layer): a city skyline, a crowd, mountains and trees, or your own PNG with a see-through sky. Fireworks and drawn scenes go behind it and the bursts light it up; bars and waveforms stay in front.

### New in 1.7.4
- **Choose the text on playlist videos:** a new "Text on the video" choice in the Playlist video form: title and "Now playing", only "Now playing", only the title, or **no text at all** (the progress bar then shows without the time). YouTube chapters are still added to the file.
- Playlist videos now follow the Title and artist layer's on/off switch.
- Bars and waveforms sit above the "Now playing" line instead of on top of it.

### New in 1.7.3
- **Moving visualizers render a little faster again:** the drawn layer (3D Pipes, particles, ring, starfield…) is laid over the background without a needless color conversion on every frame.

### New in 1.7.2
- **Video clip backgrounds no longer make thousands of pieces.** Each different piece of your clips is made once and then repeated, so an hour-long video with clips takes seconds to prepare instead of making tens of thousands of tiny files. Very short clips (a GIF, a one-frame video) now play for a few seconds each instead of flickering by 12 times a second.
- **Working files are cleaned up.** The temporary files used while making a video are deleted when it's finished, and leftovers from older versions are removed when Release Kit starts. This can free up several gigabytes.

### New in 1.7.1
- **Long videos are made 2–4 times faster.** Song videos, playlist videos and hour-long mixes with a picture background no longer re-read the picture for every frame, and the moving visualizers (3D Pipes, particles, ring, starfield…) are now drawn while the previous frames are still being encoded. A 6-minute 1080p playlist video with 3D Pipes went from about 20 minutes to 5 on a slow 2-core test machine.

### New in 1.7.0
- **Beat effects:** a new layer that hits on every beat of your song: **zoom punch**, **camera shake**, **flash**, and **colors that change with each part of the song**. Start from Gentle, Club or Hard and adjust. The title stays still while the background moves.
- **Effects on top:** rain, snow, floating dust, light leaks, lens flare and VHS tape, alone or mixed, with a strength slider.
- **Two visualizers at once:** in the Visualizer layer pick a second one (and its own color) to play behind the first, like a ring with bars underneath.

### New in 1.6.0
- **Music for these videos:** at the top of the Video tab, drop music files (or press ＋ Add music) to build the list of songs your videos use, and drag ⋮⋮ to change the order. It starts as your release's tracks; changing it doesn't change the release.
- **Playlist video:** every song in that list in one video, in order, with YouTube chapters and a **"Now playing"** title as each song starts. Choose YouTube, 4K, square, vertical, editing or web.
- **Your video clips instead of a visualizer:** in the Background layer choose **🎬 Video clips**, then drop videos (or photos) in. They **change on the beat** every 1, 2, 4 or 8 bars, or play all the way through. Turn the Visualizer layer off to show only your clips, or keep it on top.
- **Progress bar:** a new layer with a slim bar that fills as the song plays, with or without the time.
- **Drops on the Video tab go where you'd expect:** music to the music list, videos to your clips, pictures to your pictures.

### New in 1.5.3
- **3D Pipes grow at a steady speed** instead of in little start-stop pulses, and the reset when the screen fills is a slower, softer fade.

### New in 1.5.2
- **Smoother 3D Pipes:** pipes now grow continuously out of each joint instead of jumping a section at a time, speed changes ease in with the music, and each straight stretch is one seamless pipe (no more dashed look).

### New in 1.5.1
- **Videos are made on your graphics card** (NVIDIA, AMD or Intel) when your computer has one: much faster, especially 4K and long videos. Release Kit tests the card when it starts; if it ever fails, that video is redone on the processor automatically. See **Settings → Graphics card**.
- **Much bigger logos:** the size goes up to 300%, small logo files are enlarged to the size you choose, empty see-through margins around a logo are ignored, and wide logos can fill the width of the video.

### New in 1.5.0
- **A new video editor built on layers.** Your video is four layers: **Title and artist**, **Center** (your logo, the cover, a spinning record or title text), **Visualizer** and **Background**. Each layer always shows what it's set to, has an on/off switch, and opens its settings when you click it. Nothing disappears or changes when you switch between them.
- Turning a layer off and back on brings back what you had (your logo, your visualizer).
- **One place to choose the look:** the Song video form no longer has its own visualizer dropdown that could quietly override the editor.
- **Several Release Kit tabs open?** They now stay in sync instead of overwriting each other's settings.
- Saved looks moved to the top of the editor (pick one from the list, or "Save look…").

### New in 1.4.4
- **The video editor no longer hides things behind tabs.** Visualizer, Color, **Your logo**, Background, Text and Saved looks are all on screen at once. Use the "Jump to" links at the top or just scroll.
- **Your logo** has its own section with **＋ Add logo** always visible. Clicking any of your logos puts it in the middle. There's also a **＋ Logo** button next to the preview.
- Fixed: on smaller screens (or Windows display scaling) the preview could cover the controls and block clicks.
- Fixed: Settings → Save stopped working in 1.4.1–1.4.3.

### New in 1.4.3
- **Logos that just work:** pick "My logo" and your computer's normal file window opens. Any picture your browser can show works (PNG, JPG, SVG, WebP, GIF, AVIF…) and is converted for you. You can also drop a picture onto the logo area.
- **Your logos and pictures are remembered:** they're kept in a library in the editor (newest first) as thumbnails. Click one to use it, or × to remove it. They stay even if you move the original file.
- **The look is saved in Release Kit itself,** so it stays the same in any browser.
- **No logo chosen yet? The video still gets made,** using the cover until you pick a logo.
- **Queue:** two jobs run at once, so a Short or cover doesn't wait hours behind a 10-hour loop. Waiting jobs say where they are in line.
- **Schedule upload** button on every finished video: writes the title, description and tags and adds it to the upload schedule.
- The version number is shown next to the Release Kit logo.

### New in 1.4.2
- **4 retro screensaver visualizers:** 3D Maze (walk brick corridors, faster in loud parts, walls flash on the beat), 3D Pipes (pipes grow with the music), Starfield (warp speed on the kick) and Mystify (bouncing line trails). That makes 16 styles.
- **Video editor ("The look"):** a live preview that updates by itself as you change things, rendered from your actual song, and plays as a silent moving loop (turn the sound on if you like). Switch it between 16:9, 9:16, 1:1 and 4:5.
- Pick a visualizer by its **picture**; choose a **color** from swatches or any color you like.
- **Middle:** cover, spinning record, your logo or title text, with a **size slider** (logos from small to big) and how much it **pulses to the beat**.
- **Background:** cover on blur, picture fills the screen, blurred picture or dark; a **darkness** slider; **movement** (slow drift, slow zoom, breathing light, film grain).
- **Visualizer size and position** (bottom, middle or top for bars and waves).
- **Text:** turn the title/artist on or off, or write your own words for the video.
- **Saved looks:** keep your channel's style and reuse it on every release.

### New in 1.4.1
- **Clearer layout:** the tabs are numbered steps (Release → Artwork → Video → Packages → Upload → Schedule) with a **Next** button at the bottom of each. Plan, SL2 songs, Sign-ups and Settings sit apart on the right.
- **Video tab:** set **the look** once (visualizer, bump, color, picture), then pick what to make. Only that form shows. **Song video** replaces Release video and Export video, with one-click presets: YouTube, 4K, Shorts/Reels, Instagram square or 4:5, video editing, web.
- **Make full video** on every visualizer sample renders the whole track in that style.
- **Release tab:** basics and tracks first; label, (P)/(C), UPC and other distributor details are folded away. **Upload tab:** three steps: video, text, publish.
- "Browse…" buttons, smaller previews and a **Clear finished** button in the Jobs panel.
- The black window no longer prints a line for every request; it shows the private link and any errors.

### New in 1.4
- **6 new visualizers:** Radial ring (spectrum bars in a circle around your cover, disc or logo), Radial waveform, Particles that burst on the kick, Geometric shapes, a 3D-style Tunnel, and a Synthwave grid with a neon sun. They react to the beat and get bigger in louder sections like the chorus.
- **Visualizer samples:** a few seconds of all 12 visualizers with your track, cover and colors, side by side. Press **Use this** on the one you like. With no track yet, a demo beat is used. Suggestions for your genre are marked.
- **Export video:** make a visualizer (or plain) video of any song with your own settings: MP4, MOV (ProRes, for editing) or WebM; 16:9, 9:16, 1:1 or 4:5; 720p to 4K; 24 to 60 fps; draft/standard/high quality; and a start point and length for teasers.
- **More private:** Release Kit now only answers your own browser. Other websites can't control it or read your files through it. The first time, it opens with a private link and remembers your browser after that. If you ever see "Release Kit is locked", use the link in the black window or restart the app.
- Updates are checked against GitHub's checksum before installing.

### New in 1.3
- **Upload schedule** (new Schedule tab): queue videos, give them a rhythm (one a day at a set time, every 12 hours…), choose or create a playlist, and import an overnight-batch folder with titles, descriptions, tags and thumbnails. Videos upload ahead of time with a publish date, so YouTube publishes them on time even when your computer is off.
- Stays under a daily upload cap you set, pauses when YouTube's daily limit is reached and carries on later, and never opens a sign-in window in the background.
- **Connect YouTube** button with your channel name, playlist picker, and a drafted YouTube API audit request (needed for uploads to go public).
- Overnight batch now saves a thumbnail and its own cover for every track.
- **Automatic updates:** a banner when a new version is out; **Update now** installs it and reopens the app. Your work isn't touched.
- Tidier one-line tab bar.
