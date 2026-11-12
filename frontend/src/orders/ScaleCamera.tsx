import { useEffect, useRef, useState } from "react";

import { strings } from "../strings";

const t = strings.pickup.weigh;

/**
 * Takes the scale photo with the device camera, inside the app. There is no file
 * picker, so a photo can't come from the gallery.
 */
export function ScaleCamera({ onChange }: { onChange: (photo: Blob | null) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const supported = Boolean(navigator.mediaDevices?.getUserMedia);

  useEffect(() => {
    if (preview || !supported) return;
    let stream: MediaStream | null = null;
    let stopped = false;
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then((media) => {
        stream = media;
        if (stopped) media.getTracks().forEach((track) => track.stop());
        else if (video.current) video.current.srcObject = media;
      })
      .catch(() => setFailed(true));
    return () => {
      stopped = true;
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, [preview, supported]);

  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
    };
  }, [preview]);

  function capture() {
    const source = video.current;
    if (!source || !source.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = source.videoWidth;
    canvas.height = source.videoHeight;
    canvas.getContext("2d")?.drawImage(source, 0, 0);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        setPreview(URL.createObjectURL(blob));
        onChange(blob);
      },
      "image/jpeg",
      0.9,
    );
  }

  function retake() {
    setPreview(null);
    onChange(null);
  }

  if (failed || !supported) return <p className="status-error">{t.cameraFailed}</p>;
  return (
    <div className="scale-camera">
      {preview ? (
        <>
          <img src={preview} alt={t.photoAlt} />
          <button type="button" className="secondary" onClick={retake}>
            {t.retake}
          </button>
        </>
      ) : (
        <>
          <video ref={video} autoPlay playsInline muted />
          <button type="button" className="secondary" onClick={capture}>
            {t.takePhoto}
          </button>
        </>
      )}
    </div>
  );
}
