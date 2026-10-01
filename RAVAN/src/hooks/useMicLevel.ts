import { useEffect, useRef, useState } from "react";

/**
 * Returns a 0..1 amplitude from the microphone while `active` is true.
 * Falls back to a synthesized breathing amplitude when mic access is denied
 * or unavailable, so the visualisation never goes flat.
 */
export function useMicLevel(active: boolean): number {
  const [level, setLevel] = useState(0);
  const raf = useRef<number | null>(null);

  useEffect(() => {
    if (!active) {
      setLevel(0);
      return;
    }

    let cancelled = false;
    let audioCtx: AudioContext | null = null;
    let stream: MediaStream | null = null;
    let analyser: AnalyserNode | null = null;
    let data: Uint8Array | null = null;
    let t = 0;

    const loop = () => {
      if (cancelled) return;
      if (analyser && data) {
        analyser.getByteTimeDomainData(data as unknown as Uint8Array<ArrayBuffer>);
        let sum = 0;
        for (let i = 0; i < data.length; i++) {
          const v = (data[i]! - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / data.length);
        setLevel((prev) => prev * 0.6 + Math.min(1, rms * 3.4) * 0.4);
      } else {
        t += 0.06;
        const synth = 0.28 + Math.abs(Math.sin(t)) * 0.35 + Math.sin(t * 3.7) * 0.12;
        setLevel(Math.max(0, Math.min(1, synth)));
      }
      raf.current = requestAnimationFrame(loop);
    };

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        const Ctor =
          window.AudioContext ??
          (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (!Ctor) return;
        audioCtx = new Ctor();
        const src = audioCtx.createMediaStreamSource(stream);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 512;
        data = new Uint8Array(analyser.frequencyBinCount);
        src.connect(analyser);
      } catch {
        /* fall back to synthesized amplitude */
      }
    };

    void start();
    raf.current = requestAnimationFrame(loop);

    return () => {
      cancelled = true;
      if (raf.current) cancelAnimationFrame(raf.current);
      stream?.getTracks().forEach((track) => track.stop());
      void audioCtx?.close();
      setLevel(0);
    };
  }, [active]);

  return level;
}
