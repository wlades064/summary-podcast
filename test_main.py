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

    def test_webhook_values_are_stable_and_do_not_expose_token(self):
        token = "123456:secret-token"
        path, secret = main.webhook_security_values(token)
        self.assertEqual((path, secret), main.webhook_security_values(token))
        self.assertNotIn(token, path)
        self.assertNotIn(token, secret)
        self.assertTrue(path.startswith("telegram/"))


class SummaryPipelineTests(unittest.TestCase):
    @patch("main.subprocess.run")
    @patch("main.imageio_ffmpeg.get_ffmpeg_exe", return_value="C:/ffmpeg/ffmpeg.exe")
    def test_downloader_uses_current_python(self, _ffmpeg, run):
        run.return_value.returncode = 1
        run.return_value.stderr = "download failed"
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RuntimeError):
            main.download_audio("https://youtu.be/dQw4w9WgXcQ", Path(directory))
        command = run.call_args.args[0]
        self.assertEqual(command[:3], [main.sys.executable, "-m", "yt_dlp"])

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
