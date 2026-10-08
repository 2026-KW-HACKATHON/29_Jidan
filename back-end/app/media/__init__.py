"""Private media shared by manual interviews (#119) and worker Q&A (#121).

* app.media.storage: LocalMediaStorage under MEDIA_ROOT (get_media_storage / set_media_storage)
* app.media.inspection: inspect_media(bytes, "IMAGE" | "AUDIO" | "VIDEO") -> InspectedMedia or MediaRejected
* app.media.video: inspect_video, digest_video (frames, poster, sound for the AI)
* app.media.multipart: read_media_form(request) streaming multipart reader with size limits
* app.media.references: in-use checks and the photo linking protocol (lock_photos_for_link)
* app.media.retention: periodic purge of deleted/expired bytes and orphan files
* app.media.transcription: begin_transcription + the TRANSCRIPTION task handler
See back-end/docs/ai-foundation.md for usage.
"""
