# The clip in place

`web/assets/hero.mp4` (with a VP9 twin, `hero.webm`, for browsers without an
H.264 decoder) is u/pawan1995's animation of Federer's 2009 US Open tweener,
from r/tennis, cropped to 16:9 with the player controls removed and a short
fade to black at the end of the loop. `hero-poster.jpg` is its first frame:
the page paints that before the video has decoded, so the clip is the first
thing seen, never a gap. The credit line under the hero shows
whenever a clip is playing. It is their work: keep the credit, and if you
haven't yet, ask them -- a comment on the post is enough.

# Swapping in real footage

The hero plays `web/assets/hero.mp4` if that file exists, and falls back to the
generated night-session scene (`web/assets/hero.js`) if it doesn't. Nothing else
needs changing -- drop the file in and reload.

    web/assets/hero.mp4

What to use: footage you own, or footage you have licensed. Broadcast match
footage is owned by the tour and the broadcaster, and a recognisable player also
holds publicity rights over their likeness, so a clip pulled from YouTube or a
highlights reel is not usable here even with a credit. Stock libraries
(Getty, Pond5, Artgrid, Adobe Stock) licence generic night-session tennis, and
some Pexels/Coverr clips are free for commercial use -- check each clip's terms
and whether any person in it is identifiable.

Encoding that matches the layout:

    ffmpeg -i source.mov -t 12 -an \
      -vf "scale=1920:-2,crop=1920:1080" \
      -c:v libx264 -crf 24 -preset slow -pix_fmt yuv420p \
      -movflags +faststart web/assets/hero.mp4

Keep it short (8-14s), silent (`-an`, since it autoplays muted), and under
about 6 MB so the page stays quick. The scrim over it assumes a darker frame
with the action right of centre; a bright or busy left edge will fight the
headline. A clip that ends with the ball coming at the camera matches the
drawn scene it replaces.

# Vertical clips

A lot of drone footage is shot 9:16 and comes pillarboxed inside a 16:9 file.
For a top-down court there is no "up", so rotating it 90 degrees gives a true
16:9 frame with nothing cropped away. Detect the black bars first, then rotate:

    CROP=$(ffmpeg -ss 2 -t 4 -i source.mp4 -vf cropdetect=24:2:0 -f null - 2>&1 \
           | grep -o 'crop=[0-9:]*' | sort | uniq -c | sort -rn | head -1 \
           | grep -o 'crop=[0-9:]*')

    ffmpeg -i source.mp4 -t 14 -an \
      -vf "$CROP,transpose=1,scale=1920:-2" \
      -c:v libx264 -crf 24 -preset slow -pix_fmt yuv420p \
      -movflags +faststart web/assets/hero.mp4

`transpose=1` turns it clockwise; `transpose=2` the other way. Reload the page
and the hero picks the file up.

Run this on the licensed download, not the preview. Stock-library previews
carry a watermark so they cannot be used unlicensed; the file you get after
licensing is clean, and is usually 1080x1920 or larger, which lands at a full
1920x1080 after the rotation.

The scrim over the hero is tuned for dark night footage. Bright daylight clay
will fight the headline -- if it does, raise the first stop of the left-hand
gradient on `.hero-scrim` in `web/assets/styles.css` from `.82` toward `.9`.
