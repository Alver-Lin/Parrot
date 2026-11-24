import os
import tempfile

# Set this before importing backend.main so test collection cannot touch the
# running app's database, recordings, or session recovery state.
_test_data = tempfile.TemporaryDirectory(prefix="parrot-tests-")
os.environ["PARROT_DATA"] = _test_data.name
os.environ["PARROT_DISABLE_LEGACY_IMPORT"] = "1"
os.environ["PARROT_SUPPORT_DIR"] = _test_data.name
