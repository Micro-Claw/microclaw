import pytest
from microclaw.safety import SafetyConstraints, SafetyGuard, SafetyViolation, StageConstraints


@pytest.fixture
def guard():
    c = SafetyConstraints(
        stage=StageConstraints(x_min=-500, x_max=500, y_min=-500, y_max=500,
                               z_min=10, z_max=150),
    )
    return SafetyGuard(c)


class TestBoundaryExact:
    """Values at exactly the boundary must pass; one unit outside must fail."""

    def test_x_at_max(self, guard):
        guard.check_xy(500.0, 0.0)  # exactly at limit — should not raise

    def test_x_beyond_max(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_xy(500.001, 0.0)

    def test_z_at_min(self, guard):
        guard.check_z(10.0)  # exactly at limit

    def test_z_below_min(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_z(9.999)

    def test_z_at_max(self, guard):
        guard.check_z(150.0)

    def test_z_beyond_max(self, guard):
        with pytest.raises(SafetyViolation):
            guard.check_z(150.001)


class TestNoConstraints:
    """Unconstrained guard should allow anything."""

    def test_extreme_z(self):
        guard = SafetyGuard(SafetyConstraints())
        guard.check_z(1_000_000.0)  # no exception

    def test_all_channels(self):
        guard = SafetyGuard(SafetyConstraints())
        guard.check_channel("AnythingAtAll")


class TestFromYaml:
    def test_loads_yaml(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "stage:\n  z_min: 5.0\n  z_max: 100.0\n"
            "camera:\n  max_exposure_ms: 500\n"
        )
        constraints = SafetyConstraints.from_yaml(str(cfg))
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation):
            guard.check_z(0.0)
        with pytest.raises(SafetyViolation):
            guard.check_exposure(600.0)

    def test_empty_yaml_gives_no_constraints(self, tmp_path):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        constraints = SafetyConstraints.from_yaml(str(cfg))
        guard = SafetyGuard(constraints)
        guard.check_z(999999.0)  # no exception

    def test_forbidden_property_loaded(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text(
            "forbidden_properties:\n"
            "  - device: Core\n"
            "    property: Initialize\n"
        )
        constraints = SafetyConstraints.from_yaml(str(cfg))
        guard = SafetyGuard(constraints)
        with pytest.raises(SafetyViolation, match="forbidden"):
            guard.check_property("Core", "Initialize")

    def test_channels_loaded(self, tmp_path):
        cfg = tmp_path / "safety.yaml"
        cfg.write_text("channels:\n  allowed: [DAPI, FITC]\n")
        constraints = SafetyConstraints.from_yaml(str(cfg))
        guard = SafetyGuard(constraints)
        guard.check_channel("DAPI")  # no exception
        with pytest.raises(SafetyViolation):
            guard.check_channel("GFP")
