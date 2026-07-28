from microclaw.hooks import HookBase
import numpy as np


class MosaicCellCounter(HookBase):
    """Accumulate tiles into a common-coordinate mosaic and count cell-sized
    connected components across tile seams, so a cell straddling several tiles
    is counted once. Logs a running unique-cell total; the final entry is the
    count for the whole scanned area."""

    def __init__(self, pixel_size_um=0.105, min_area_um2=20.0,
                 max_area_um2=1000.0, snr_min=3.0, log_path=None):
        super().__init__(log_path)
        self.px = float(pixel_size_um)
        self.min_area_um2 = float(min_area_um2)
        self.max_area_um2 = float(max_area_um2)
        self.snr_min = float(snr_min)
        # position label -> (x_um, y_um, image ndarray)
        self.tiles = {}

    def _threshold(self, img):
        # Otsu threshold on this tile; returns boolean foreground mask.
        f = img.astype(np.float64)
        lo, hi = f.min(), f.max()
        if hi <= lo:
            return np.zeros(img.shape, dtype=bool)
        hist, edges = np.histogram(f, bins=256, range=(lo, hi))
        total = f.size
        w = np.cumsum(hist)
        centers = 0.5 * (edges[:-1] + edges[1:])
        s = np.cumsum(hist * centers)
        best_t, best_var = centers[0], -1.0
        for i in range(1, 256):
            wb = w[i - 1]
            wf = total - wb
            if wb == 0 or wf == 0:
                continue
            mb = s[i - 1] / wb
            mf = (s[-1] - s[i - 1]) / wf
            var = wb * wf * (mb - mf) ** 2
            if var > best_var:
                best_var, best_t = var, centers[i - 1]
        return f > best_t

    def _snr(self, img):
        f = img.astype(np.float64)
        med = np.median(f)
        mad = np.median(np.abs(f - med)) + 1e-9
        return (f.max() - med) / (1.4826 * mad)

    def _label(self, mask):
        # 4-connectivity connected components, iterative flood fill.
        h, w = mask.shape
        labels = np.zeros((h, w), dtype=np.int32)
        cur = 0
        for y in range(h):
            for x in range(w):
                if mask[y, x] and labels[y, x] == 0:
                    cur += 1
                    stack = [(y, x)]
                    labels[y, x] = cur
                    while stack:
                        cy, cx = stack.pop()
                        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] \
                                    and labels[ny, nx] == 0:
                                labels[ny, nx] = cur
                                stack.append((ny, nx))
        return labels, cur

    def _count_mosaic(self):
        if not self.tiles:
            return 0, []
        xs = [v[0] for v in self.tiles.values()]
        ys = [v[1] for v in self.tiles.values()]
        any_img = next(iter(self.tiles.values()))[2]
        th, tw = any_img.shape
        x0, y0 = min(xs), min(ys)
        span_x = (max(xs) - x0) / self.px + tw + 2
        span_y = (max(ys) - y0) / self.px + th + 2
        canvas = np.zeros((int(span_y), int(span_x)), dtype=bool)
        for (xu, yu, img) in self.tiles.values():
            if self._snr(img) < self.snr_min:
                continue
            m = self._threshold(img)
            cx = int(round((xu - x0) / self.px))
            cy = int(round((yu - y0) / self.px))
            canvas[cy:cy + th, cx:cx + tw] |= m
        labels, n = self._label(canvas)
        px_area = self.px * self.px
        objs = []
        for lab in range(1, n + 1):
            area_px = int(np.count_nonzero(labels == lab))
            area_um2 = area_px * px_area
            if self.min_area_um2 <= area_um2 <= self.max_area_um2:
                objs.append(area_um2)
        return len(objs), objs

    def image_process_fn(self, image, metadata, event_queue):
        pos = metadata.get("Axes", {}).get("position",
                                           metadata.get("PositionName"))
        xu = metadata.get("XPosition_um_Intended")
        yu = metadata.get("YPosition_um_Intended")
        if xu is not None and yu is not None and pos is not None:
            self.tiles[pos] = (float(xu), float(yu), np.asarray(image))
        count, areas = self._count_mosaic()
        self.log(metadata,
                 tiles_seen=len(self.tiles),
                 tile_snr=round(float(self._snr(np.asarray(image))), 2),
                 running_cell_count=count,
                 mean_cell_area_um2=round(float(np.mean(areas)), 1) if areas else 0.0)
        self._write_log()
        return image, metadata