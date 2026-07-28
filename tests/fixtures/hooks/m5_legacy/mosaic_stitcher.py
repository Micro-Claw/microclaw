from microclaw.hooks import HookBase
import numpy as np


class MosaicStitcher(HookBase):
    """Accumulate raw tile pixels into a common-coordinate mosaic using each
    tile's intended stage XY and a known pixel size, and write the assembled
    mosaic as a 16-bit TIFF once all n_tiles have arrived. Overlapping regions
    take the most recent tile's pixels (display mosaic, not blended)."""

    def __init__(self, pixel_size_um=0.105, n_tiles=36,
                 out_path="mosaic.tiff", log_path=None):
        super().__init__(log_path)
        self.px = float(pixel_size_um)
        self.n_tiles = int(n_tiles)
        self.out_path = out_path
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

    def _write_tiff(self, canvas):
        try:
            import tifffile
            tifffile.imwrite(self.out_path, canvas)
            return "tifffile"
        except Exception:
            np.save(self.out_path + ".npy", canvas)
            return "npy_fallback"

    def image_process_fn(self, image, metadata, event_queue):
        pos = metadata.get("Axes", {}).get("position",
                                           metadata.get("PositionName"))
        xu = metadata.get("XPosition_um_Intended")
        yu = metadata.get("YPosition_um_Intended")
        if xu is not None and yu is not None and pos is not None:
            self.tiles[pos] = (float(xu), float(yu), np.asarray(image))
        entry = {"tiles_seen": len(self.tiles), "wrote_mosaic": False}
        if len(self.tiles) >= self.n_tiles and not self.written:
            canvas = self._assemble()
            writer = self._write_tiff(canvas)
            self.written = True
            entry["wrote_mosaic"] = True
            entry["mosaic_path"] = self.out_path
            entry["mosaic_shape"] = list(canvas.shape)
            entry["writer"] = writer
        self.log(metadata, **entry)
        self._write_log()
        return image, metadata