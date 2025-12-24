
import unittest

class TestPlatformCheck(unittest.TestCase):
    def test_any_in_manylinux(self):
        filename = "numpy-1.26.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
        platform = "win_amd64"
        
        # This is the current logic
        should_skip = False
        # Proposed fix: check for '-any'
        should_skip_fix = False
        if platform not in filename and '-any' not in filename:
            should_skip_fix = True
            
        print(f"Should skip (fix logic): {should_skip_fix}")
        self.assertTrue(should_skip_fix, "Fix works: correctly skips manylinux")

        # Verify it still accepts valid any wheels
        valid_any = "requests-2.31.0-py3-none-any.whl"
        should_skip_valid = False
        if platform not in valid_any and '-any' not in valid_any:
            should_skip_valid = True
        self.assertFalse(should_skip_valid, "Fix works: correctly accepts valid any wheel")

if __name__ == '__main__':
    unittest.main()
