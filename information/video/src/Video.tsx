import type { FC, ReactNode } from "react";
import { AbsoluteFill, Easing, interpolate, Sequence, useCurrentFrame, useVideoConfig } from "remotion";

export const FPS = 30;
export const TOTAL = 60 * FPS;

const INK = "#1C1B18";
const MUTED = "#6F6B63";
const FAINT = "#98948B";
const BG = "#F3F1EC";
const SURFACE = "#FFFCF8";
const LINE = "#E5E1D8";
const GREEN = "#1A6B45";
const GREEN_BG = "#E7F3EC";
const BLUE = "#1D4E86";
const BLUE_BG = "#E8F0F8";
const AMBER = "#8A5810";
const AMBER_BG = "#F8F0E2";
const PANEL = "#E9E4DA";

const serif = '"Liberation Serif", "Noto Serif", Georgia, serif';
const sans = '"Liberation Sans", "Noto Sans", sans-serif';

const enter = (frame: number, delay = 0) => {
  const opacity = interpolate(frame, [delay, delay + 16], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const y = interpolate(frame, [delay, delay + 18], [26, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  return { opacity, transform: `translateY(${y}px)` };
};

const Stage: FC<{ children: ReactNode }> = ({ children }) => (
  <AbsoluteFill style={{ background: BG, fontFamily: sans, color: INK }}>{children}</AbsoluteFill>
);

const Kicker: FC<{ children: string }> = ({ children }) => (
  <div style={{ fontSize: 22, letterSpacing: 3, color: FAINT, fontWeight: 700 }}>{children}</div>
);

const Cover: FC = () => {
  const frame = useCurrentFrame();
  const steps = [
    { n: "01", name: "Profil", color: GREEN },
    { n: "02", name: "Axtarış", color: BLUE },
    { n: "03", name: "CV", color: AMBER },
    { n: "04", name: "Göndəriş", color: INK },
  ];
  const line = interpolate(frame, [12, 70], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <Stage>
      <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 18, background: INK }} />
      <div style={{ position: "absolute", left: 90, top: 120, ...enter(frame, 4) }}>
        <Kicker>JOB AUTO-FLOW</Kicker>
        <div style={{ fontFamily: serif, fontSize: 92, lineHeight: 0.98, marginTop: 28, maxWidth: 820 }}>
          Vakansiyadan
          <br />
          müraciətə bir axın
        </div>
        <div style={{ marginTop: 36, fontSize: 30, lineHeight: 1.4, color: MUTED, maxWidth: 760 }}>
          On iki iş portalı. Son otuz gün. Dörd addım.
        </div>
      </div>
      <div style={{ position: "absolute", left: 90, bottom: 110, display: "flex", gap: 72 }}>
        {[
          [12, "iş portalı"],
          [30, "günlük tarix"],
          [4, "ekran addımı"],
        ].map(([value, label], index) => {
          const shown = Math.round(
            interpolate(frame, [24 + index * 6, 52 + index * 6], [0, Number(value)], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
          );
          return (
            <div key={String(label)} style={enter(frame, 18 + index * 6)}>
              <div style={{ fontFamily: serif, fontSize: 72 }}>{String(shown).padStart(2, "0")}</div>
              <div style={{ color: MUTED, fontSize: 22 }}>{label}</div>
            </div>
          );
        })}
      </div>
      <div
        style={{
          position: "absolute",
          right: 80,
          top: 80,
          width: 520,
          height: 920,
          background: PANEL,
          borderRadius: 36,
          ...enter(frame, 8),
        }}
      >
        <div
          style={{
            position: "absolute",
            left: 118,
            top: 150,
            width: 6,
            height: 620 * line,
            background: "#D4CFC4",
            borderRadius: 6,
          }}
        />
        {steps.map((step, index) => (
          <div
            key={step.n}
            style={{
              position: "absolute",
              left: 78,
              top: 120 + index * 190,
              display: "flex",
              alignItems: "center",
              gap: 28,
              ...enter(frame, 16 + index * 10),
            }}
          >
            <div
              style={{
                width: 86,
                height: 86,
                borderRadius: 43,
                background: step.color,
                color: SURFACE,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontWeight: 700,
                fontSize: 26,
              }}
            >
              {step.n}
            </div>
            <div style={{ fontFamily: serif, fontSize: 42 }}>{step.name}</div>
          </div>
        ))}
      </div>
    </Stage>
  );
};

const Problem: FC = () => {
  const frame = useCurrentFrame();
  const cards = [
    { n: "01", title: "Səpələnmiş siyahılar", body: "Eyni vəzifə on iki saytda, hər birinin öz axtarışı var.", mark: "dots" },
    { n: "02", title: "Köhnə arxiv", body: "Aylarla əvvəlki elanlar siyahının üstündə durur.", mark: "layers" },
    { n: "03", title: "Ümumi CV", body: "Bir fayl hər elana eyni gedir.", mark: "page" },
    { n: "04", title: "İtən müraciət", body: "E-poçtlu müraciətlə əl ilə yazılan elan qarışır.", mark: "mail" },
  ];
  return (
    <Stage>
      <div style={{ position: "absolute", left: 80, top: 70, ...enter(frame) }}>
        <Kicker>01  —  MƏSƏLƏ</Kicker>
        <div style={{ fontFamily: serif, fontSize: 68, marginTop: 12 }}>Axtarış hər saytda ayrı işdir</div>
      </div>
      <div style={{ position: "absolute", left: 80, right: 80, top: 280, display: "flex", gap: 24 }}>
        {cards.map((item, index) => (
          <div
            key={item.n}
            style={{
              flex: 1,
              height: 680,
              background: SURFACE,
              border: `1px solid ${LINE}`,
              borderRadius: 28,
              padding: 32,
              ...enter(frame, 8 + index * 8),
            }}
          >
            <div style={{ fontFamily: serif, fontSize: 32, color: FAINT }}>{item.n}</div>
            <Mark kind={item.mark} />
            <div style={{ fontSize: 32, fontWeight: 700, marginTop: 28 }}>{item.title}</div>
            <div style={{ fontSize: 26, color: MUTED, marginTop: 16, lineHeight: 1.35 }}>{item.body}</div>
          </div>
        ))}
      </div>
    </Stage>
  );
};

const Mark: FC<{ kind: string }> = ({ kind }) => {
  if (kind === "dots") {
    return (
      <div style={{ display: "grid", gridTemplateColumns: "18px 18px", gap: 10, marginTop: 36, width: 46 }}>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} style={{ width: 18, height: 18, borderRadius: 9, background: INK }} />
        ))}
      </div>
    );
  }
  if (kind === "layers") {
    return (
      <div style={{ position: "relative", height: 90, marginTop: 28 }}>
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            style={{
              position: "absolute",
              left: i * 14,
              top: i * 14,
              width: 92,
              height: 52,
              borderRadius: 10,
              border: `2px solid ${INK}`,
              background: i === 2 ? SURFACE : "#E4DED4",
            }}
          />
        ))}
      </div>
    );
  }
  if (kind === "page") {
    return (
      <div style={{ width: 78, height: 100, border: `2px solid ${INK}`, borderRadius: 8, marginTop: 28, padding: 12 }}>
        {[70, 54, 64].map((w) => (
          <div key={w} style={{ height: 6, width: w, background: FAINT, borderRadius: 4, marginBottom: 10 }} />
        ))}
      </div>
    );
  }
  return (
    <div style={{ width: 100, height: 68, border: `2px solid ${INK}`, borderRadius: 10, marginTop: 36, position: "relative" }}>
      <div
        style={{
          position: "absolute",
          left: 18,
          top: 14,
          width: 0,
          height: 0,
          borderLeft: "32px solid transparent",
          borderRight: "32px solid transparent",
          borderTop: "26px solid #1C1B18",
        }}
      />
    </div>
  );
};

const Steps: FC = () => {
  const frame = useCurrentFrame();
  const items = [
    { n: "01", title: "Profil", body: "CV-dən profil və axtarış sözləri çıxır.", color: GREEN },
    { n: "02", title: "Vakansiyalar", body: "Portallar gəzilir, təzə elanlar seçilir.", color: BLUE },
    { n: "03", title: "CV və məktub", body: "Seçilmiş elan üçün bir səhifə yazılır.", color: AMBER },
    { n: "04", title: "Göndəriş", body: "Məktub gedir, qalanı Excel-ə düşür.", color: INK },
  ];
  const line = interpolate(frame, [10, 50], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <Stage>
      <div style={{ position: "absolute", left: 90, top: 80, ...enter(frame) }}>
        <Kicker>02  —  AXIN</Kicker>
        <div style={{ fontFamily: serif, fontSize: 72, marginTop: 12 }}>Dörd addım, bir ekran</div>
      </div>
      <div style={{ position: "absolute", left: 180, right: 180, top: 470, height: 8, background: "#E4DED4", borderRadius: 8 }}>
        <div style={{ width: `${line * 100}%`, height: "100%", background: "#C9C2B6", borderRadius: 8 }} />
      </div>
      <div style={{ position: "absolute", left: 100, right: 100, top: 400, display: "flex", justifyContent: "space-between" }}>
        {items.map((item, index) => (
          <div key={item.n} style={{ width: 360, textAlign: "center", ...enter(frame, 12 + index * 8) }}>
            <div
              style={{
                width: 130,
                height: 130,
                borderRadius: 65,
                background: item.color,
                color: SURFACE,
                margin: "0 auto",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 36,
                fontWeight: 700,
              }}
            >
              {item.n}
            </div>
            <div style={{ fontFamily: serif, fontSize: 36, marginTop: 28 }}>{item.title}</div>
            <div style={{ fontSize: 24, color: MUTED, marginTop: 12, lineHeight: 1.35 }}>{item.body}</div>
          </div>
        ))}
      </div>
    </Stage>
  );
};

const Path: FC = () => {
  const frame = useCurrentFrame();
  const row1 = [
    ["cv.pdf", "Namizəd"],
    ["Profil", "Keş"],
    ["12 portal", "Axtarış"],
    ["30 gün", "Tarix"],
  ];
  const row2 = [
    ["Bal", "0–100"],
    ["CV + məktub", "Bir səhifə"],
    ["E-poçt", "və ya Excel"],
  ];
  const Pill: FC<{ head: string; sub: string; n: number; delay: number }> = ({ head, sub, n, delay }) => (
    <div
      style={{
        width: 340,
        height: 130,
        background: SURFACE,
        border: `1px solid ${LINE}`,
        borderRadius: 24,
        display: "flex",
        alignItems: "center",
        gap: 18,
        padding: "0 22px",
        ...enter(frame, delay),
      }}
    >
      <div
        style={{
          width: 54,
          height: 54,
          borderRadius: 27,
          background: INK,
          color: SURFACE,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontWeight: 700,
        }}
      >
        {n}
      </div>
      <div>
        <div style={{ fontSize: 28, fontWeight: 700 }}>{head}</div>
        <div style={{ fontSize: 22, color: MUTED }}>{sub}</div>
      </div>
    </div>
  );
  return (
    <Stage>
      <div style={{ position: "absolute", left: 90, top: 70, ...enter(frame) }}>
        <Kicker>03  —  YOL</Kicker>
        <div style={{ fontFamily: serif, fontSize: 68, marginTop: 10 }}>Fayldan hesabata</div>
      </div>
      <div style={{ position: "absolute", left: 80, top: 300, display: "flex", alignItems: "center", gap: 16 }}>
        {row1.map((item, index) => (
          <div key={item[0]} style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <Pill head={item[0]} sub={item[1]} n={index + 1} delay={8 + index * 6} />
            {index < row1.length - 1 ? <Arrow /> : null}
          </div>
        ))}
      </div>
      <div style={{ position: "absolute", left: 250, top: 500, fontSize: 42, ...enter(frame, 36) }}>↓</div>
      <div style={{ position: "absolute", left: 220, top: 580, display: "flex", alignItems: "center", gap: 16 }}>
        {row2.map((item, index) => (
          <div key={item[0]} style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <Pill head={item[0]} sub={item[1]} n={index + 5} delay={40 + index * 6} />
            {index < row2.length - 1 ? <Arrow /> : null}
          </div>
        ))}
      </div>
    </Stage>
  );
};

const Arrow: FC = () => <div style={{ fontSize: 36, color: INK }}>→</div>;

const Score: FC = () => {
  const frame = useCurrentFrame();
  const words = ["Data Analitik", "Hesabat", "Power BI", "SQL", "Maliyyə"];
  const width = interpolate(frame, [18, 70], [0, 80], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <Stage>
      <div style={{ position: "absolute", left: 90, top: 70, ...enter(frame) }}>
        <Kicker>04  —  AXTARIŞ VƏ BAL</Kicker>
        <div style={{ fontFamily: serif, fontSize: 64, marginTop: 10 }}>Sözlər CV-dən, hədd 80</div>
      </div>
      <div style={{ position: "absolute", left: 90, top: 250, display: "flex", gap: 18, flexWrap: "wrap", width: 900 }}>
        {words.map((word, index) => (
          <div
            key={word}
            style={{
              background: GREEN_BG,
              borderRadius: 999,
              padding: "18px 28px",
              fontSize: 30,
              fontWeight: 700,
              ...enter(frame, 6 + index * 5),
            }}
          >
            {word}
          </div>
        ))}
      </div>
      <div
        style={{
          position: "absolute",
          left: 90,
          right: 90,
          top: 470,
          background: SURFACE,
          borderRadius: 28,
          border: `1px solid ${LINE}`,
          padding: "36px 40px",
          ...enter(frame, 16),
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", color: FAINT, fontSize: 22 }}>
          <span>0</span>
          <span>100</span>
        </div>
        <div style={{ marginTop: 16, height: 28, background: "#E7E2D8", borderRadius: 14, position: "relative" }}>
          <div style={{ width: `${width}%`, height: "100%", background: GREEN, borderRadius: 14 }} />
          <div
            style={{
              position: "absolute",
              left: `calc(${width}% - 22px)`,
              top: -10,
              width: 48,
              height: 48,
              borderRadius: 24,
              background: INK,
            }}
          />
        </div>
        <div style={{ marginTop: 22, color: GREEN, fontWeight: 700, fontSize: 26 }}>Seçim həddi 80</div>
      </div>
      <div style={{ position: "absolute", left: 90, bottom: 80, display: "flex", gap: 24 }}>
        <Note fill={BLUE_BG} title="Staj və junior" body="Bu başlıqlar siyahıya düşmür." delay={28} frame={frame} />
        <Note fill={AMBER_BG} title="Sırf mühasib" body="Bal 35-i keçmir." delay={36} frame={frame} />
      </div>
    </Stage>
  );
};

const Note: FC<{ fill: string; title: string; body: string; delay: number; frame: number }> = ({
  fill,
  title,
  body,
  delay,
  frame,
}) => (
  <div style={{ width: 520, background: fill, borderRadius: 24, padding: "22px 28px", ...enter(frame, delay) }}>
    <div style={{ fontFamily: serif, fontSize: 32 }}>{title}</div>
    <div style={{ fontSize: 24, color: MUTED, marginTop: 8 }}>{body}</div>
  </div>
);

const Close: FC = () => {
  const frame = useCurrentFrame();
  const steps = ["Profili yadda saxla", "Skanı başlat", "CV hazırla", "Yoxla, sonra göndər"];
  return (
    <Stage>
      <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 18, background: INK }} />
      <div style={{ position: "absolute", left: 90, top: 64, ...enter(frame) }}>
        <Kicker>05  —  CV VƏ GÖNDƏRİŞ</Kicker>
        <div style={{ fontFamily: serif, fontSize: 60, marginTop: 8 }}>Elanın dilində, bir səhifə</div>
      </div>
      <div style={{ position: "absolute", left: 90, top: 230, display: "flex", gap: 28 }}>
        <Paper title="CV" accent={GREEN} widths={[420, 300, 380, 240]} delay={6} frame={frame} caption="Bir A4. Faktlar CV-dəndir." />
        <Paper title="Məktub" accent={BLUE} widths={[400, 440, 280, 360]} delay={12} frame={frame} caption="Elanın dili hər iki mətnin dilidir." />
      </div>
      <div style={{ position: "absolute", left: 90, right: 80, bottom: 70, display: "flex", gap: 20 }}>
        {steps.map((label, index) => (
          <div
            key={label}
            style={{
              flex: 1,
              background: SURFACE,
              border: `1px solid ${LINE}`,
              borderRadius: 22,
              padding: "18px 22px",
              ...enter(frame, 28 + index * 5),
            }}
          >
            <div style={{ fontFamily: serif, fontSize: 36 }}>{String(index + 1).padStart(2, "0")}</div>
            <div style={{ fontSize: 22, color: MUTED, marginTop: 6 }}>{label}</div>
          </div>
        ))}
      </div>
    </Stage>
  );
};

const Paper: FC<{ title: string; accent: string; widths: number[]; delay: number; frame: number; caption: string }> = ({
  title,
  accent,
  widths,
  delay,
  frame,
  caption,
}) => (
  <div
    style={{
      width: 820,
      height: 390,
      background: SURFACE,
      borderRadius: 28,
      border: `1px solid ${LINE}`,
      overflow: "hidden",
      display: "flex",
      ...enter(frame, delay),
    }}
  >
    <div style={{ width: 14, background: accent }} />
    <div style={{ padding: "28px 36px" }}>
      <div style={{ fontFamily: serif, fontSize: 36 }}>{title}</div>
      {widths.map((w) => (
        <div key={w} style={{ height: 14, width: w, background: "#E7E2D8", borderRadius: 8, marginTop: 18 }} />
      ))}
      <div style={{ marginTop: 22, color: MUTED, fontSize: 22 }}>{caption}</div>
    </div>
  </div>
);

export const JobAutoFlow: FC = () => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const progress = interpolate(frame, [0, durationInFrames - 1], [0, 100], {
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ background: BG }}>
      <Sequence durationInFrames={8 * FPS}>
        <Cover />
      </Sequence>
      <Sequence from={8 * FPS} durationInFrames={10 * FPS}>
        <Problem />
      </Sequence>
      <Sequence from={18 * FPS} durationInFrames={14 * FPS}>
        <Steps />
      </Sequence>
      <Sequence from={32 * FPS} durationInFrames={12 * FPS}>
        <Path />
      </Sequence>
      <Sequence from={44 * FPS} durationInFrames={8 * FPS}>
        <Score />
      </Sequence>
      <Sequence from={52 * FPS} durationInFrames={8 * FPS}>
        <Close />
      </Sequence>
      <div style={{ position: "absolute", left: 0, bottom: 0, height: 8, width: `${progress}%`, background: INK }} />
    </AbsoluteFill>
  );
};
