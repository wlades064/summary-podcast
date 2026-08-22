import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main


class NormalizeYoutubeUrlTests(unittest.TestCase):
    def test_supported_urls(self):
        expected = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        for url in (
            "https://youtu.be/dQw4w9WgXcQ?t=10",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=abc",
            "https://youtube.com/shorts/dQw4w9WgXcQ",
            "https://m.youtube.com/live/dQw4w9WgXcQ?feature=share",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    main.normalize_youtube_url(url),
                    (expected, "dQw4w9WgXcQ"),
                )

    def test_rejects_non_youtube_and_invalid_id(self):
        for url in ("https://example.com/video", "https://youtu.be/short"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                main.normalize_youtube_url(url)


class SummaryPipelineTests(unittest.TestCase):
    def test_direct_youtube_is_preferred(self):
        client = Mock()
        with patch.object(main, "create_gemini_client", return_value=client), patch.object(
            main, "summarize_youtube_url", return_value="готово"
        ) as direct, patch.object(main, "summarize_audio_fallback") as fallback:
            result = main.generate_summary("https://youtu.be/dQw4w9WgXcQ", Path("."))
        self.assertEqual(result, ("готово", False))
        direct.assert_called_once()
        fallback.assert_not_called()

    def test_audio_is_used_when_direct_call_fails(self):
        client = Mock()
        with patch.object(main, "create_gemini_client", return_value=client), patch.object(
            main, "summarize_youtube_url", side_effect=RuntimeError("direct failed")
        ), patch.object(main, "summarize_audio_fallback", return_value="резерв") as fallback:
            result = main.generate_summary("https://youtu.be/dQw4w9WgXcQ", Path("."))
        self.assertEqual(result, ("резерв", True))
        fallback.assert_called_once()

    def test_document_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "summary.docx"
            main.create_document(
                "# Главные мысли\n- Первый тезис",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                target,
            )
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
