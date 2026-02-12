VideoDiff
=========

A program to attempt to test a video source for temporal dithering and other visual artifacts

## License

GPLv2 (see LICENSE)

## Warning

Documentation is incomplete and more work needs to be done

```
usage: videodiff.py [-h] [--fill-value FILL_VALUE]
                   [--dither-method {r,g,b,a,m,n}] [--display] [--output OUTPUT]
                   [--cap CAP | --file FILE]

Compare frames from a video or capture device

optional arguments:
  -h, --help            show this help message and exit
  --fill-value FILL_VALUE
                        Used with mask method, fill value for detected image
                        changes.
  --dither-method {r,g,b,a,m,n}, -x {r,g,b,a,m,n}
                        Dither detection method
  --display, -d
  --output OUTPUT, -o OUTPUT
                        Output directory for sequential image output
  --cap CAP             Index value of cv2.VideoCapture device
  --file FILE           Path to AVI file to use instead of a video device
```

Capture tuning options (mainly for `--cap`): `--backend`, `--fourcc`, `--width`, `--height`, `--fps`.

Example (1080p60, YUY2, DirectShow): `python videodiff.py --cap 1 --backend dshow --fourcc YUY2 --width 1920 --height 1080 --fps 60 -d`

## GUI

```
pip install -r requirements.txt
python videodiff_gui.py
```

## Requirements

- Python >= 3.8
- NumPy
- OpenCV with Python bindings
- ffmpeg (need to test how library linking works)

## Tested platforms
Windows x86_64

Linux x86_64 (Gentoo)

## Keybindings

`r`: Switch to the red channel

`g`: Switch to the green channel

`b`: Switch to the blue channel

`m`: Switch to mask mode

`a`: Switch to absolute subtraction mode

`p`: Switch to frame-to-frame mode

`c`: Switch back to normal playback mode

`n`: Display normal image

`q`: Quit
