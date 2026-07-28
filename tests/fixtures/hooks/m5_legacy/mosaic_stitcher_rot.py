from microclaw.hooks import HookBase
import numpy as np


class MosaicStitcherRot(HookBase):
    """Stitch tiles into a common-coordinate mosaic, rotating each tile's pixels
    by rot90_k counter-clockwise quarter-turns (and optionally flipping) to align
    the camera's pixel axes with the stage axes before placement. Placement uses
    each tile's intended stage XY at a known pixel size; overlaps take the most
    recent tile. Writes a 16-bit TIFF once all n_tiles have arrived."""

    def __init__(self, pixel_size_um=0.105, n_tiles=36, rot90_k=1,
                 flip_after=False, out_path="mosaic.tiff", log_path=None):
        super().__init__(log_path)
        self.px = float(pixel_size_um)
        self.n_tiles = int(n_tiles)
        self.rot90_k = int(rot90_k) % 4
        self.flip_after = bool(flip_after)
        self.out_path = out_path
        self.tiles = {}
        self.written = False

    def _orient(self, img):
        out = np.rot90(np.asarray(img), self.rot90_k)
        if self.flip_after:
            out = np.fliplr(out)
        return np.ascontiguousarray(out)

    def _assemble(self):
        xs = [v[0] for v in self.tiles.values()]
        ys = [v[1] for v in self.tiles.values()]
        sample = self._orient(next(iter(self.tiles.values()))[2])
        th, tw = sample.shape
        dtype = sample.dtype
        x0, y0 = min(xs), min(ys)
        span_x = int(round((max(xs) - x0) / self.px)) + tw + 2
        span_y = int(round((max(ys) - y0) / self.px)) + th + 2
        canvas = np.zeros((span_y, span_x), dtype=dtype)
        for (xu, yu, img) in self.tiles.values():
            t = self._orient(img)
            cx = int(round((xu - x0) / self.px))
            cy = int(round((yu - y0) / self.px))
            canvas[cy:cy + t.shape[0], cx:cx + t.shape[1]] = t
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
            entry["rot90_k"] = self.rot90_k
            entry["flip_after"] = self.flip_after
            entry["writer"] = writer
        self.log(metadata, **entry)
        self._write_log()
        return image, metadata