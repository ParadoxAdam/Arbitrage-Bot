"""Tests for the reference anchor lookup table."""
import pytest
from app.valuation.reference_anchors import (
    find_anchor, all_anchors, ReferenceAnchor,
)


class TestAnchorLookup:
    def test_iphone_14_pro_128_found(self):
        a = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
        assert a is not None
        assert a.storage_gb == 128
        assert a.low > 0 and a.mid > a.low and a.high > a.mid

    def test_iphone_14_pro_max_128_found(self):
        a = find_anchor("phones", "Apple", "iphone 14 pro max", 128, "unlocked")
        assert a is not None
        # Pro Max should be more expensive than Pro
        pro = find_anchor("phones", "Apple", "iphone 14 pro", 128, "unlocked")
        assert a.mid > pro.mid

    def test_carrier_locked_not_found(self):
        a = find_anchor("phones", "Apple", "iphone 14 pro", 128, "at&t")
        assert a is None

    def test_unknown_brand_not_found(self):
        a = find_anchor("phones", "Samsung", "galaxy s23", 256, "unlocked")
        assert a is None

    def test_unknown_storage_not_found(self):
        a = find_anchor("phones", "Apple", "iphone 14 pro", 999, "unlocked")
        assert a is None


class TestAnchorIntegrity:
    def test_all_anchors_have_required_fields(self):
        for a in all_anchors():
            assert a.category
            assert a.brand
            assert a.model
            assert a.low > 0 and a.mid > 0 and a.high > 0
            assert a.low <= a.mid <= a.high
            assert a.last_updated      # ISO date
            assert a.source_label

    def test_anchors_are_immutable(self):
        a = all_anchors()[0]
        with pytest.raises(Exception):
            a.low = 999.0   # frozen dataclass should reject

    def test_storage_progression_is_sane(self):
        """For each model, larger storage should be ≥ smaller storage."""
        from collections import defaultdict
        by_model = defaultdict(list)
        for a in all_anchors():
            by_model[a.model].append((a.storage_gb or 0, a.mid))
        for model, items in by_model.items():
            items.sort()
            for i in range(1, len(items)):
                assert items[i][1] >= items[i-1][1] - 10, (
                    f"Storage progression broken for {model}: {items}"
                )
