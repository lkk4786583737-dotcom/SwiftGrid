import unittest

import pandas as pd

import engine


class GeneratedCodeGuardrailTests(unittest.TestCase):
    def test_allows_in_memory_dataframe_transform(self):
        code = """
def process_data(source_dfs):
    frame = next(iter(source_dfs.values())).copy()
    frame["double"] = frame["value"] * 2
    return frame
"""
        result = engine.run_process_data(code, {"source": pd.DataFrame({"value": [2, 4]})})
        self.assertEqual(result["double"].tolist(), [4, 8])

    def test_rejects_file_access(self):
        code = """
def process_data(source_dfs):
    open("leak.txt", "w").write("unsafe")
    return next(iter(source_dfs.values()))
"""
        with self.assertRaises(ValueError):
            engine.run_process_data(code, {"source": pd.DataFrame({"value": [1]})})

    def test_rejects_unbounded_while_loop(self):
        code = """
def process_data(source_dfs):
    while True:
        pass
"""
        with self.assertRaises(ValueError):
            engine.validate_generated_code(code)


if __name__ == "__main__":
    unittest.main()
