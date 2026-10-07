import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('retention', Path(__file__).resolve().parents[1] / 'scripts/retention.py')
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)
PREFIX = 'ghcr.io/2026-kw-hackathon/29_jidan-frontend'


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'dev/frontend'
        (self.base / 'releases').mkdir(parents=True)

    def release(self, n, verified=True):
        path = self.base / 'releases' / f'release.{n:08d}'
        path.mkdir()
        (path / '.env').write_text(f'IMAGE_REF={PREFIX}@sha256:{n:064x}\n')
        if verified:
            (path / 'verified').touch()
        os.utime(path, (n, n))
        return path

    def test_keep_latest_five_plus_older_current(self):
        paths = [self.release(i) for i in range(8)]
        (self.base / 'current').symlink_to(paths[0])
        retention.prune_releases(self.base)
        self.assertTrue(paths[0].exists())
        self.assertFalse(paths[1].exists())
        self.assertFalse(paths[2].exists())
        self.assertTrue(all(p.exists() for p in paths[3:]))

    def test_pending_prevents_any_deletion(self):
        paths = [self.release(i) for i in range(8)]
        (self.base / 'pending').symlink_to(paths[-1])
        retention.prune_releases(self.base)
        self.assertTrue(all(p.exists() for p in paths))

    def test_failed_releases_do_not_evict_verified_releases(self):
        good = [self.release(i) for i in range(5)]
        bad = [self.release(i, False) for i in range(5, 12)]
        retention.prune_releases(self.base)
        self.assertTrue(all(p.exists() for p in good))
        self.assertFalse(any(p.exists() for p in bad))

    def test_foreign_directory_and_symlink_are_untouched(self):
        foreign = self.base / 'releases/release.foreign'
        foreign.mkdir()
        (foreign / '.env').write_text('IMAGE_REF=someone/else:latest\n')
        link = self.base / 'releases/release.symlink'
        link.symlink_to(self.root)
        retention.prune_releases(self.base)
        self.assertTrue(foreign.exists())
        self.assertTrue(link.is_symlink())

    def test_other_environment_refs_are_protected(self):
        self.release(0)
        other = self.root / 'production/backend/releases/release.00000000'
        other.mkdir(parents=True)
        ref = 'ghcr.io/2026-kw-hackathon/29_jidan-backend@sha256:' + 'a' * 64
        (other / '.env').write_text(f'IMAGE_REF={ref}\n')
        self.assertIn(ref, retention.retained_refs(self.root))

    def test_images_preserve_container_ids_and_retained_digests(self):
        images = [dict(ref=f'{PREFIX}:{i:040x}', id=f'id{i}', created=f'{i:02d}') for i in range(10)]
        removed = retention.removal_candidates(images, {'id0'}, {images[1]['ref']})
        self.assertEqual(removed, [images[i]['ref'] for i in (4, 3, 2)])

    def test_foreign_images_and_non_sha_tags_are_never_removed(self):
        images = [dict(ref=r, id=r, created='0') for r in ['nginx:latest', PREFIX + ':latest',
                  'ghcr.io/2026-kw-hackathon/other:' + 'a' * 40]]
        self.assertEqual(retention.removal_candidates(images, set(), set(), keep=0), [])

    def test_frontend_and_backend_have_independent_budgets(self):
        images = [dict(ref=f'{PREFIX}:{i:040x}', id=f'f{i}', created=str(i)) for i in range(6)]
        backend = dict(ref=PREFIX.replace('frontend', 'backend') + ':' + 'a' * 40, id='b', created='0')
        images.append(backend)
        self.assertEqual(retention.removal_candidates(images, set(), set()), [images[0]['ref']])


if __name__ == '__main__':
    unittest.main()
