---
name: view-attachment
description: View image or file attachments that you can't directly see. Use this skill when you receive a message with attachments listed as file paths and you need to understand their contents. Especially useful for text-only models that cannot process images natively.
---

# view-attachment

When a message includes attachments, you see file paths like:
```
attachments:
  - state/attachments/12345-screenshot.png
```

These are real files on disk. You can't see them inline, but you have several ways
to inspect them depending on the file type.

## Step 0: Try `read_file` Directly First

Before reaching for OCR, metadata, or any of the workarounds below: just call
`read_file` on the attachment path. Many models (including this one, depending on
provider/runtime) are natively multimodal — `read_file` returns images, PDFs, and
other binary attachments as multimodal content blocks you can see and reason about
directly, the same way you'd see an image pasted into a chat.

```
read_file(file_path="state/attachments/12345-screenshot.png")
```

Don't assume you're text-only just because you're inside an agent harness. Try it
first, and only fall back to Step 1's file-type triage and the OCR/metadata tricks
in Step 2 if `read_file` genuinely fails to give you usable content (e.g., a format
it can't decode, or a model that truly has no vision capability). Misdiagnosing your
own capability and skipping straight to indirect fallbacks wastes steps and can
produce worse (hallucinated or guessed) answers than just looking at the file.

## Step 1: Identify the File Type

Check the extension:
- **Images:** `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`, `.bmp`
- **Text/code:** `.txt`, `.md`, `.py`, `.js`, `.json`, `.csv`, `.log`, `.yaml`, `.toml`
- **Documents:** `.pdf`
- **Other:** anything else

## Step 2: Choose a Viewing Strategy

### For Text-Based Files
Read them directly — these are just text:
```bash
cat state/attachments/12345-notes.txt
```

### For Images (When You Can't View Them Natively)

Try these approaches in order of usefulness:

**Option A: Describe from context**
Often the surrounding message gives enough context. If someone says "look at this error"
and attaches a screenshot, the error is probably in their message text too. Ask if unclear.

**Option B: File metadata**
Get dimensions, format, and size to understand what you're looking at:
```bash
file state/attachments/12345-image.png
identify state/attachments/12345-image.png 2>/dev/null  # if ImageMagick available
```

**Option C: OCR with Tesseract**
Extract text content from screenshots, documents, or photos of text:
```bash
tesseract state/attachments/12345-screenshot.png stdout 2>/dev/null
```

**Option D: Convert to SVG (for simple graphics/charts)**
Trace bitmap images into vector format you can reason about:
```bash
# Install if needed: apt-get install -y potrace
convert state/attachments/12345-chart.png pbm:- | potrace --svg -o - 2>/dev/null
```
SVG output is XML text — you can read the paths and shapes.

**Option E: Base64 encode**
If another tool or API accepts base64 images, encode it:
```bash
base64 -w0 state/attachments/12345-image.png
```
Warning: base64 output is large. Only use this if you're passing it to a vision-capable
API or tool, not for your own comprehension.

**Option F: Pixel sampling (last resort)**
For very simple images, sample pixel values at key coordinates:
```bash
# Get the color at specific pixel coordinates (requires ImageMagick)
convert state/attachments/12345-image.png -format '%[pixel:p{100,100}]' info: 2>/dev/null
```

### For PDFs
```bash
# Extract text
pdftotext state/attachments/12345-document.pdf - 2>/dev/null
```

## Step 3: Be Honest About Limitations

If none of these approaches give you useful information:
1. Say what you tried
2. Describe what you CAN tell (file size, type, metadata)
3. Ask the human to describe what's in the attachment

Never pretend you can see an image when you can't. Never hallucinate image contents.
