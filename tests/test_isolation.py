"""
Tests for configuration isolation and default save folder settings.
"""

import os
import shutil
import tempfile
from pathlib import Path

def test_youcut_home_isolation():
    test_dir = tempfile.mkdtemp(prefix="youcut_test_")
    try:
        os.environ["YOUCUT_HOME"] = test_dir
        
        # Test settings
        from clipgrab.config.settings import AppSettings, get_youcut_dir
        assert str(get_youcut_dir()) == str(Path(test_dir).resolve())
        
        s = AppSettings.load()
        assert Path(s.last_output_folder) == Path.home() / "Downloads" or s.last_output_folder == str(Path.home())
        assert "test_out" not in s.last_output_folder
        assert "scratch" not in s.last_output_folder
        
        # Test modifying settings in isolation
        s.update(theme="dark", concurrent_fragments=8)
        assert (Path(test_dir) / "config.json").is_file()
        
        # Test history_db isolation
        from clipgrab.data.history_db import HistoryDB
        db = HistoryDB()
        assert Path(db._db_path).parent == Path(test_dir).resolve()
        
        # Test logger isolation
        from clipgrab.core.logger import get_log_directory
        assert get_log_directory() == Path(test_dir).resolve()
        
        print("ALL ISOLATION CHECKS PASSED!")
    finally:
        os.environ.pop("YOUCUT_HOME", None)
        shutil.rmtree(test_dir, ignore_errors=True)

if __name__ == "__main__":
    test_youcut_home_isolation()

