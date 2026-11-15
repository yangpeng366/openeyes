# OpenCV Research: How Open-Source Projects Use CV for UI Automation

## Summary

Researched via codebuddy-client (hy4free, 105 turns) and codex-client (ccx).
Both sub-agents analyzed real project source code on GitHub.

## Key Projects Examined

| Project | CV Approach | OpenCV Role |
|---|---|---|
| SikuliX | TM_CCOEFF_NORMED pyramid matching + Canny(0.66s, 1.33s, L2grad) + MORPH_ELLIPSE close + RETR_CCOMP | Core |
| guibot | ContourFinder: adaptiveThreshold(11,2) default, matchShapes, tunable CVParameter | Core |
| OmniParser v2 | YOLO detector + PaddleOCR fusion, cv2.resize/cvtColor only | Minimal |
| computer-use-mcp | Canny(50,150) + findContours + approxPolyDP(0.02*peri) + circularity | Core |
| SAM-ScreenParser | RapidOCR + UIA at OCR center, OpenCV for drawing only | Minimal |
| screenparse | DOM extraction + VLM classification, no CV | None |

## Findings Applied to OpenEyes (this session)

### Implemented and tested (full suite green: 1289 passed, 7 skipped, 2026-11-15):

1. **Local-maxima suppression before NMS** (anchors.py): dilate-based peak detection
   collapses each match cluster before O(n^2) Python NMS runs.

2. **uint8 for matchTemplate** (anchors.py): removed unnecessary float32 conversion;
   OpenCV SIMD paths for 8-bit are faster.

3. **cv2.minMaxLoc** in _best_match_score (anchors.py): replaces mask+filter+max
   with a single OpenCV call.

4. **approxPolyDP + circularity** in contour classification (scene.py): vertex count
   and circularity distinguish triangles (caret), circles, and squares. Matches
   computer-use-mcp's 0.02*peri epsilon exactly.

5. **O(n^2) dedup fix** (scene.py): replaced shapes.index(shape) with enumerate.

6. **Morphology: MORPH_ELLIPSE + 1 iteration** (scene.py): switched from MORPH_RECT(5,5)x2
   to MORPH_ELLIPSE(5,5)x1. SikuliX uses ellipse; codex analysis showed 2 iterations
   merged adjacent controls on 150% DPI.

### Validated by research but not yet implemented:

- **Adaptive Canny via Otsu**: derive thresholds from each frame's contrast distribution.
  guibot defaults to adaptiveThreshold(11,2) instead of Canny entirely.
- **RETR_TREE/RETR_CCOMP** instead of RETR_EXTERNAL: for nested container detection.
  SikuliX uses CCOMP; no project uses RETR_TREE for UI elements.
- **DPI-scaled thresholds**: area/size filters must scale by dpi^2 on high-DPI displays.
  SAM-ScreenParser records dpi_scale and coordinate_space in output.
- **OCR preprocessing**: 2x upscaling + CLAHE contrast enhancement before RapidOCR.
- **OCR-contour fusion**: OmniParser's remove_overlap_new uses max(IoU, containment ratios)
  instead of plain IoU to handle "label inside button" case.

## Key Insight

No project classifies widget types from contour shape alone. Semantics always come
from OCR, UIA, trained detector, or VLM. OpenEyes' structured-first chain
(uia -> ocr -> opencv) is validated by every modern project examined.

## Sources

- intra2net/guibot: finder.py:647-1019, config.py:68
- microsoft/OmniParser: util/utils.py, omnitool/
- saint1729/SikuliX-VisionProxy: src/finder.cpp, src/cvgui.cpp
- slothitude/computer-use-mcp: computer_use_mcp.py
- Sabir-Ali-Mondal/SAM-ScreenParser: CodeBase/test_screen.py