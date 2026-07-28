from microclaw.hook_decisions import EmitArtifact, HookResult
import numpy as np


class MosaicStitcher:
    """Accumulate raw tile pixels into a common-coordinate mosaic using each
    tile's intended stage XY and a known pixel size, and write the assembled
    mosaic as a 16-bit TIFF once all n_tiles have arrived. Overlapping regions
    take the most recent tile's pixels (display mosaic, not blended)."""

    def __init__(self, pixel_size_um=0.105, n_tiles=36,
                 filename="mosaic.tiff"):
        self.px = float(pixel_size_um)
        self.n_tiles = int(n_tiles)
        self.filename = filename
        # position label -> (x_um, y_um, image ndarray)
        self.tiles = {}
        self.written = False

    def _assemble(self):
        xs = [v[0] for v in self.tiles.values()]
        ys = [v[1] for v in self.tiles.values()]
        any_img = next(iter(self.tiles.values()))[2]
        th, tw = any_img.shape
        dtype = any_img.dtype
        x0, y0 = min(xs), min(ys)
        span_x = int(round((max(xs) - x0) / self.px)) + tw + 2
        span_y = int(round((max(ys) - y0) / self.px)) + th + 2
        canvas = np.zeros((span_y, span_x), dtype=dtype)
        for (xu, yu, img) in self.tiles.values():
            cx = int(round((xu - x0) / self.px))
            cy = int(round((yu - y0) / self.px))
            canvas[cy:cy + th, cx:cx + tw] = img
        return canvas

    def analyze_frame(self, image, metadata):
        pos = metadata.get("Axes", {}).get("position",
                                           metadata.get("PositionName"))
        xu = metadata.get("XPosition_um_Intended")
        yu = metadata.get("YPosition_um_Intended")
        if xu is not None and yu is not None and pos is not None:
            self.tiles[pos] = (float(xu), float(yu), np.asarray(image))
        entry = {"tiles_seen": len(self.tiles), "wrote_mosaic": False}
        actions = ()
        if len(self.tiles) >= self.n_tiles and not self.written:
            canvas = self._assemble()
            self.written = True
            actions = (EmitArtifact(self.filename, canvas),)
            entry["wrote_mosaic"] = True
            entry["mosaic_filename"] = self.filename
            entry["mosaic_shape"] = list(canvas.shape)
        return HookResult(entry, actions)
