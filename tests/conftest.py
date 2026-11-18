import os
import tempfile

# Set this before importing backend.main so test collection cannot touch the
# running app's database, recordings, or session recovery state.
_test_data = tempfile.TemporaryDirectory(prefix="parrot-tests-")
os.environ["PARROT_DATA"] = _test_data.name
