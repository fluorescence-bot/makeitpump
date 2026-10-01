import type { Channel, RotatorState, SourceChannel } from "./types";
import { DEFAULT_SPEED, ROTATE_MAX_DEG, rotate } from "./flow";
import "./scene.css";

export interface Scene {
  setNames(names: Record<Channel, string>): void;
  onNameChange(cb: (ch: Channel, name: string) => void): void;
  startRain(ch: SourceChannel, ml: number, s: number): void;
  startPour(ml: number, s: number): void;
  setPaused(paused: boolean): void;
  stopAll(): void;
  onDose(cb: (ch: Channel, ml: number, speed: number) => void): void;
  setTotals(totals: Record<Channel, number>): void;
  resetBowl(): void;
  getBowl(): { A: number; B: number; C: number; total: number };
  setRotator(state: RotatorState, deg: number | null, target?: number, message?: string): void; // mug tilts to the angle
  onRotate(cb: (deg: number) => void): void;          // click on the robot while ready; deg = its "rotate to" field
  onRotatorConnect(cb: () => void): void;             // click on the robot while disconnected / in error
}

// Parse hex color to RGB
function hexToRgb(hex: string): [number, number, number] {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) return [200, 200, 200];
  return [parseInt(result[1], 16), parseInt(result[2], 16), parseInt(result[3], 16)];
}

// Blend colors based on volumes
function blendColors(
  a: number,
  b: number,
  c: number,
  colA: string,
  colB: string,
  colC: string
): string {
  const total = a + b + c;
  if (total === 0) return "var(--col-d-empty, #EDE7FA)";

  const [rA, gA, bA] = hexToRgb(colA);
  const [rB, gB, bB] = hexToRgb(colB);
  const [rC, gC, bC] = hexToRgb(colC);

  const rBlend = Math.round((rA * a + rB * b + rC * c) / total);
  const gBlend = Math.round((gA * a + gB * b + gC * c) / total);
  const bBlend = Math.round((bA * a + bB * b + bC * c) / total);

  return `rgb(${rBlend}, ${gBlend}, ${bBlend})`;
}

// Get CSS variable value
function getCSSVar(varName: string, fallback: string): string {
  try {
    return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || fallback;
  } catch {
    return fallback;
  }
}

export function createScene(container: HTMLElement): Scene {
  const bowlState = { A: 0, B: 0, C: 0 };
  let paused = false;

  const nameChangeCallbacks: ((ch: Channel, name: string) => void)[] = [];
  const doseCallbacks: ((ch: Channel, ml: number, speed: number) => void)[] = [];
  // mL pumped by one click and the speed (0-100 %) it pumps at, per channel (edited in the scene)
  const doseMl: Record<Channel, number> = { A: 1, B: 1, C: 1, D: 1 };
  const doseSpeed: Record<Channel, number> = { A: DEFAULT_SPEED, B: DEFAULT_SPEED, C: DEFAULT_SPEED, D: DEFAULT_SPEED };
  const names: Record<Channel, string> = { A: "A", B: "B", C: "C", D: "D" };

  // Create a stage wrapper for the SVG and inputs overlay
  const stage = document.createElement("div");
  stage.classList.add("rm-stage");

  // SVG creation
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 900 620");
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", "100%");
  svg.classList.add("rm-scene");

  // Defs for gradients and filters
  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");

  // Sky gradient
  const skyGradient = document.createElementNS("http://www.w3.org/2000/svg", "linearGradient");
  skyGradient.id = "skyGradient";
  skyGradient.setAttribute("x1", "0%");
  skyGradient.setAttribute("y1", "0%");
  skyGradient.setAttribute("x2", "0%");
  skyGradient.setAttribute("y2", "100%");
  const skyStop1 = document.createElementNS("http://www.w3.org/2000/svg", "stop");
  skyStop1.setAttribute("offset", "0%");
  skyStop1.setAttribute("stop-color", "var(--bg-top, #F4F0FF)");
  const skyStop2 = document.createElementNS("http://www.w3.org/2000/svg", "stop");
  skyStop2.setAttribute("offset", "100%");
  skyStop2.setAttribute("stop-color", "var(--bg-bottom, #FFF6FB)");
  skyGradient.appendChild(skyStop1);
  skyGradient.appendChild(skyStop2);
  defs.appendChild(skyGradient);

  // Liquid clip path
  const liquidClip = document.createElementNS("http://www.w3.org/2000/svg", "clipPath");
  liquidClip.id = "liquidClip";
  const liquidClipPathElem = document.createElementNS("http://www.w3.org/2000/svg", "path");
  liquidClipPathElem.id = "liquidClipPath";
  liquidClip.appendChild(liquidClipPathElem);
  defs.appendChild(liquidClip);

  svg.appendChild(defs);

  // Background
  const bgRect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  bgRect.setAttribute("width", "900");
  bgRect.setAttribute("height", "620");
  bgRect.setAttribute("fill", "url(#skyGradient)");
  svg.appendChild(bgRect);

  // Sparkles in the sky (4-6 tiny twinkling sparkles)
  const sparklesGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  sparklesGroup.classList.add("rm-sparkles");
  const sparklePositions = [
    { x: 120, y: 40 },
    { x: 280, y: 50 },
    { x: 580, y: 60 },
    { x: 780, y: 35 },
    { x: 650, y: 80 },
  ];
  sparklePositions.forEach((pos, i) => {
    const sparkle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    sparkle.setAttribute("cx", pos.x.toString());
    sparkle.setAttribute("cy", pos.y.toString());
    sparkle.setAttribute("r", "1.5");
    sparkle.setAttribute("fill", "var(--muted, #A99BC4)");
    sparkle.setAttribute("opacity", "0.7");
    sparkle.classList.add(`rm-sparkle-${i}`);
    sparklesGroup.appendChild(sparkle);
  });
  svg.appendChild(sparklesGroup);

  // Clouds group
  const cloudsGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  cloudsGroup.classList.add("rm-clouds");

  const cloudData: Array<{
    ch: SourceChannel;
    x: number;
    y: number;
    color: string;
    inkColor: string;
  }> = [
    { ch: "A", x: 170, y: 120, color: "var(--col-a, #A8E8F0)", inkColor: "var(--col-a-ink, #4FB3C6)" },
    { ch: "B", x: 450, y: 120, color: "var(--col-b, #FFB8D9)", inkColor: "var(--col-b-ink, #E07AAE)" },
    { ch: "C", x: 730, y: 120, color: "var(--col-c, #FFE7A0)", inkColor: "var(--col-c-ink, #D9B34A)" },
  ];

  const cloudElements: Record<SourceChannel, { group: SVGGElement; eyes: { left: SVGCircleElement; right: SVGCircleElement } }> = {
    A: null as any,
    B: null as any,
    C: null as any,
  };

  for (const cloudDef of cloudData) {
    const cloudGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
    cloudGroup.classList.add(`rm-cloud`, `rm-cloud-${cloudDef.ch}`);
    cloudGroup.setAttribute("data-channel", cloudDef.ch);

    // Cloud body - big fluffy cloud made of overlapping circles (~190x110 units)
    const cloudPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const cx = cloudDef.x;
    const cy = cloudDef.y;
    cloudPath.setAttribute(
      "d",
      `M ${cx - 95} ${cy + 20}
       C ${cx - 95} ${cy - 20}, ${cx - 50} ${cy - 50}, ${cx - 20} ${cy - 50}
       C ${cx + 10} ${cy - 55}, ${cx + 30} ${cy - 45}, ${cx + 50} ${cy - 50}
       C ${cx + 80} ${cy - 50}, ${cx + 95} ${cy - 20}, ${cx + 95} ${cy + 20}
       C ${cx + 95} ${cy + 50}, ${cx + 70} ${cy + 55}, ${cx} ${cy + 55}
       C ${cx - 70} ${cy + 55}, ${cx - 95} ${cy + 50}, ${cx - 95} ${cy + 20} Z`
    );
    cloudPath.setAttribute("fill", cloudDef.color);
    cloudPath.setAttribute("stroke", cloudDef.inkColor);
    cloudPath.setAttribute("stroke-width", "2.5");
    cloudGroup.appendChild(cloudPath);

    // Cloud face
    const faceGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
    faceGroup.classList.add("rm-cloud-face");

    // Eyes (bigger, r≈5)
    const eye1 = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    eye1.setAttribute("cx", (cx - 25).toString());
    eye1.setAttribute("cy", (cy - 5).toString());
    eye1.setAttribute("r", "5");
    eye1.setAttribute("fill", "var(--ink, #5B4B7A)");
    eye1.classList.add("rm-eye", "rm-eye-left");
    faceGroup.appendChild(eye1);

    const eye2 = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    eye2.setAttribute("cx", (cx + 25).toString());
    eye2.setAttribute("cy", (cy - 5).toString());
    eye2.setAttribute("r", "5");
    eye2.setAttribute("fill", "var(--ink, #5B4B7A)");
    eye2.classList.add("rm-eye", "rm-eye-right");
    faceGroup.appendChild(eye2);

    // Mouth (cute smile/ω shape)
    const mouth = document.createElementNS("http://www.w3.org/2000/svg", "path");
    mouth.setAttribute("d", `M ${cx - 10} ${cy + 15} Q ${cx} ${cy + 22} ${cx + 10} ${cy + 15}`);
    mouth.setAttribute("stroke", "var(--ink, #5B4B7A)");
    mouth.setAttribute("stroke-width", "2");
    mouth.setAttribute("fill", "none");
    mouth.setAttribute("stroke-linecap", "round");
    mouth.setAttribute("stroke-linejoin", "round");
    mouth.classList.add("rm-mouth");
    faceGroup.appendChild(mouth);

    // Blush ovals (left and right)
    const blushLeft = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
    blushLeft.setAttribute("cx", (cx - 55).toString());
    blushLeft.setAttribute("cy", (cy + 10).toString());
    blushLeft.setAttribute("rx", "12");
    blushLeft.setAttribute("ry", "8");
    blushLeft.setAttribute("fill", cloudDef.inkColor);
    blushLeft.setAttribute("opacity", "0.3");
    faceGroup.appendChild(blushLeft);

    const blushRight = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
    blushRight.setAttribute("cx", (cx + 55).toString());
    blushRight.setAttribute("cy", (cy + 10).toString());
    blushRight.setAttribute("rx", "12");
    blushRight.setAttribute("ry", "8");
    blushRight.setAttribute("fill", cloudDef.inkColor);
    blushRight.setAttribute("opacity", "0.3");
    faceGroup.appendChild(blushRight);

    cloudGroup.appendChild(faceGroup);

    // Letter badge (small circle on top-right of cloud)
    const badge = document.createElementNS("http://www.w3.org/2000/svg", "g");
    badge.classList.add("rm-badge");
    const badgeBg = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    badgeBg.setAttribute("cx", (cx + 75).toString());
    badgeBg.setAttribute("cy", (cy - 40).toString());
    badgeBg.setAttribute("r", "16");
    badgeBg.setAttribute("fill", cloudDef.inkColor);
    badgeBg.setAttribute("stroke", "white");
    badgeBg.setAttribute("stroke-width", "2");
    badge.appendChild(badgeBg);
    const badgeText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    badgeText.setAttribute("x", (cx + 75).toString());
    badgeText.setAttribute("y", (cy - 33).toString());
    badgeText.setAttribute("text-anchor", "middle");
    badgeText.setAttribute("font-size", "20");
    badgeText.setAttribute("font-weight", "700");
    badgeText.setAttribute("fill", "white");
    badgeText.setAttribute("font-family", "var(--font, system-ui)");
    badgeText.textContent = cloudDef.ch;
    badge.appendChild(badgeText);
    cloudGroup.appendChild(badge);

    // Drops container
    const dropsContainer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    dropsContainer.classList.add(`rm-drops-${cloudDef.ch}`);
    cloudGroup.appendChild(dropsContainer);

    cloudsGroup.appendChild(cloudGroup);

    cloudElements[cloudDef.ch] = {
      group: cloudGroup,
      eyes: {
        left: eye1,
        right: eye2,
      },
    };
  }

  svg.appendChild(cloudsGroup);

  // Bowl - wide funnel shape
  const bowlGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  bowlGroup.classList.add("rm-bowl");

  // Bowl rim (ellipse at top ~190-710, y≈330, ry≈45)
  const rimOuter = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
  rimOuter.setAttribute("cx", "450");
  rimOuter.setAttribute("cy", "330");
  rimOuter.setAttribute("rx", "260");
  rimOuter.setAttribute("ry", "45");
  rimOuter.setAttribute("fill", "none");
  rimOuter.setAttribute("stroke", "var(--col-d-ink, #9A7FD6)");
  rimOuter.setAttribute("stroke-width", "3");
  bowlGroup.appendChild(rimOuter);

  // Bowl inner rim (visible ellipse)
  const rimInner = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
  rimInner.setAttribute("cx", "450");
  rimInner.setAttribute("cy", "332");
  rimInner.setAttribute("rx", "250");
  rimInner.setAttribute("ry", "40");
  rimInner.setAttribute("fill", "var(--col-d-empty, #EDE7FA)");
  rimInner.setAttribute("stroke", "none");
  bowlGroup.appendChild(rimInner);

  // Bowl body (funnel sides - closed shape)
  const bowlBody = document.createElementNS("http://www.w3.org/2000/svg", "path");
  bowlBody.setAttribute(
    "d",
    "M 190 330 L 450 520 M 710 330 L 450 520"
  );
  bowlBody.setAttribute("fill", "none");
  bowlBody.setAttribute("stroke", "var(--col-d-ink, #9A7FD6)");
  bowlBody.setAttribute("stroke-width", "3");
  bowlBody.setAttribute("stroke-linecap", "round");
  bowlGroup.appendChild(bowlBody);

  // Liquid
  const liquidGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  liquidGroup.classList.add("rm-liquid");

  // Liquid surface (ellipse)
  const liquidPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  liquidPath.id = "liquidPath";
  liquidPath.setAttribute("fill", "rgb(200, 200, 200)");
  liquidGroup.appendChild(liquidPath);

  bowlGroup.appendChild(liquidGroup);

  // Bowl face
  const bowlFaceGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  bowlFaceGroup.classList.add("rm-bowl-face");

  const bowlEye1 = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  bowlEye1.setAttribute("cx", "420");
  bowlEye1.setAttribute("cy", "350");
  bowlEye1.setAttribute("r", "5");
  bowlEye1.setAttribute("fill", "var(--ink, #5B4B7A)");
  bowlFaceGroup.appendChild(bowlEye1);

  const bowlEye2 = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  bowlEye2.setAttribute("cx", "480");
  bowlEye2.setAttribute("cy", "350");
  bowlEye2.setAttribute("r", "5");
  bowlEye2.setAttribute("fill", "var(--ink, #5B4B7A)");
  bowlFaceGroup.appendChild(bowlEye2);

  const bowlMouth = document.createElementNS("http://www.w3.org/2000/svg", "path");
  bowlMouth.setAttribute("d", "M 420 375 Q 450 385 480 375");
  bowlMouth.setAttribute("stroke", "var(--ink, #5B4B7A)");
  bowlMouth.setAttribute("stroke-width", "2");
  bowlMouth.setAttribute("fill", "none");
  bowlMouth.setAttribute("stroke-linecap", "round");
  bowlMouth.setAttribute("stroke-linejoin", "round");
  bowlFaceGroup.appendChild(bowlMouth);

  bowlGroup.appendChild(bowlFaceGroup);

  // Spout (tube from bottom to cup)
  const spout = document.createElementNS("http://www.w3.org/2000/svg", "path");
  spout.setAttribute("d", "M 450 520 L 450 540");
  spout.setAttribute("stroke", "var(--col-d-ink, #9A7FD6)");
  spout.setAttribute("stroke-width", "5");
  spout.setAttribute("fill", "none");
  spout.setAttribute("stroke-linecap", "round");
  bowlGroup.appendChild(spout);

  svg.appendChild(bowlGroup);

  // Tube from spout to cup
  const tubeGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  tubeGroup.classList.add("rm-tube");

  const tube = document.createElementNS("http://www.w3.org/2000/svg", "path");
  tube.setAttribute("d", "M 450 540 Q 520 545 600 535");
  tube.setAttribute("stroke", "var(--col-d-ink, #9A7FD6)");
  tube.setAttribute("stroke-width", "6");
  tube.setAttribute("fill", "none");
  tube.setAttribute("stroke-linecap", "round");
  tubeGroup.appendChild(tube);

  // Animated drops on tube
  const tubeDropsGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  tubeDropsGroup.classList.add("rm-tube-drops");
  tubeGroup.appendChild(tubeDropsGroup);

  svg.appendChild(tubeGroup);

  // Cup (bigger, ~70 wide, with handle and face)
  const cupGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  cupGroup.classList.add("rm-cup");
  // Everything but the label tilts with the cuvette rotator (pivot: bottom of the mug)
  const cupTilt = document.createElementNS("http://www.w3.org/2000/svg", "g");
  cupTilt.classList.add("rm-cup-tilt");
  cupGroup.appendChild(cupTilt);

  // Cup body (about 70 wide)
  const cupBody = document.createElementNS("http://www.w3.org/2000/svg", "path");
  cupBody.setAttribute("d", "M 570 540 L 640 540 L 640 570 Q 640 580 630 580 L 580 580 Q 570 580 570 570 Z");
  cupBody.setAttribute("fill", "var(--col-d-empty, #EDE7FA)");
  cupBody.setAttribute("stroke", "var(--col-d-ink, #9A7FD6)");
  cupBody.setAttribute("stroke-width", "2.5");
  cupTilt.appendChild(cupBody);

  // Cup handle
  const cupHandle = document.createElementNS("http://www.w3.org/2000/svg", "path");
  cupHandle.setAttribute("d", "M 640 550 Q 670 560 640 570");
  cupHandle.setAttribute("stroke", "var(--col-d-ink, #9A7FD6)");
  cupHandle.setAttribute("stroke-width", "3");
  cupHandle.setAttribute("fill", "none");
  cupHandle.setAttribute("stroke-linecap", "round");
  cupTilt.appendChild(cupHandle);

  // Cup face (small eyes and smile)
  const cupEye1 = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  cupEye1.setAttribute("cx", "590");
  cupEye1.setAttribute("cy", "555");
  cupEye1.setAttribute("r", "2");
  cupEye1.setAttribute("fill", "var(--ink, #5B4B7A)");
  cupTilt.appendChild(cupEye1);

  const cupEye2 = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  cupEye2.setAttribute("cx", "620");
  cupEye2.setAttribute("cy", "555");
  cupEye2.setAttribute("r", "2");
  cupEye2.setAttribute("fill", "var(--ink, #5B4B7A)");
  cupTilt.appendChild(cupEye2);

  const cupMouth = document.createElementNS("http://www.w3.org/2000/svg", "path");
  cupMouth.setAttribute("d", "M 590 565 Q 605 570 620 565");
  cupMouth.setAttribute("stroke", "var(--ink, #5B4B7A)");
  cupMouth.setAttribute("stroke-width", "1");
  cupMouth.setAttribute("fill", "none");
  cupMouth.setAttribute("stroke-linecap", "round");
  cupTilt.appendChild(cupMouth);

  const cupLabel = document.createElementNS("http://www.w3.org/2000/svg", "text");
  cupLabel.setAttribute("x", "605");
  cupLabel.setAttribute("y", "600");
  cupLabel.setAttribute("text-anchor", "middle");
  cupLabel.setAttribute("font-size", "16");
  cupLabel.setAttribute("font-weight", "700");
  cupLabel.setAttribute("fill", "var(--ink, #5B4B7A)");
  cupLabel.setAttribute("font-family", "var(--font, system-ui)");
  cupLabel.textContent = "final solution ☕";
  cupGroup.appendChild(cupLabel);

  svg.appendChild(cupGroup);

  // Cuvette rotator robot, bottom right next to the mug: its arm "rotates" the mug.
  // Click it to rotate to the angle in its field, or to (re)connect when it is offline.
  const robotGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  robotGroup.classList.add("rm-robot", "rm-interactive", "rm-robot-off");
  const robotHit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  robotHit.setAttribute("x", "690");
  robotHit.setAttribute("y", "512");
  robotHit.setAttribute("width", "105");
  robotHit.setAttribute("height", "98");
  robotHit.setAttribute("fill", "transparent");
  robotGroup.appendChild(robotHit);
  const robotHint = document.createElementNS("http://www.w3.org/2000/svg", "text");
  robotHint.setAttribute("x", "742");
  robotHint.setAttribute("y", "528");
  robotHint.setAttribute("text-anchor", "middle");
  robotHint.classList.add("rm-dose-hint");
  robotGroup.appendChild(robotHint);
  const robotArm = document.createElementNS("http://www.w3.org/2000/svg", "text");
  robotArm.setAttribute("x", "722");
  robotArm.setAttribute("y", "576");
  robotArm.setAttribute("text-anchor", "middle");
  robotArm.classList.add("rm-robot-emoji", "rm-robot-arm");
  robotArm.textContent = "🦾";
  robotGroup.appendChild(robotArm);
  const robotFace = document.createElementNS("http://www.w3.org/2000/svg", "text");
  robotFace.setAttribute("x", "764");
  robotFace.setAttribute("y", "576");
  robotFace.setAttribute("text-anchor", "middle");
  robotFace.classList.add("rm-robot-emoji", "rm-robot-face");
  robotFace.textContent = "🤖";
  robotGroup.appendChild(robotFace);
  const robotAngle = document.createElementNS("http://www.w3.org/2000/svg", "text");
  robotAngle.setAttribute("x", "742");
  robotAngle.setAttribute("y", "602");
  robotAngle.setAttribute("text-anchor", "middle");
  robotAngle.classList.add("rm-robot-angle");
  robotGroup.appendChild(robotAngle);
  svg.appendChild(robotGroup);

  let rotatorState: RotatorState = "disconnected";
  let rotatorDeg: number | null = null;
  let rotatorTarget: number | null = null;
  let rotateDeg = 30; // angle a click on the robot rotates to (edited in the scene)
  let cupAngle = 0; // drawn tilt, eases towards cupTarget in the animation loop
  let cupTarget = 0;
  const rotateCallbacks: ((deg: number) => void)[] = [];
  const connectCallbacks: (() => void)[] = [];

  const renderRobot = () => {
    const off = rotatorState === "disconnected" || rotatorState === "error";
    const busy = rotatorState === "moving" || rotatorState === "homing";
    robotGroup.classList.toggle("rm-robot-off", off);
    robotGroup.classList.toggle("rm-robot-busy", busy);
    robotHint.textContent = off ? "click to connect" : busy ? "" : `click to rotate to ${rotateDeg}°`;
    robotAngle.textContent =
      rotatorState === "disconnected" ? "not connected" :
      rotatorState === "error" ? "rotator error" :
      rotatorState === "homing" ? "homing…" :
      rotatorState === "moving" ? `${Math.round(cupAngle)}° → ${rotatorTarget}°` :
      `${rotatorDeg ?? 0}°`;
  };
  renderRobot();

  robotGroup.addEventListener("click", () => {
    if (rotatorState === "disconnected" || rotatorState === "error") connectCallbacks.forEach((cb) => cb());
    else if (rotatorState === "ready") rotateCallbacks.forEach((cb) => cb(rotateDeg));
  });


  // Create a group for name inputs in the SVG using foreignObject
  const inputsGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  inputsGroup.classList.add("rm-inputs-group");
  svg.appendChild(inputsGroup);

  // Create name inputs for clouds and bowl (positioned using SVG foreignObject)
  const createNameInput = (ch: Channel, svgX: number, svgY: number) => {
    const foreignObj = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
    foreignObj.setAttribute("x", (svgX - 75).toString()); // Center horizontally (75 = half of 150px width)
    foreignObj.setAttribute("y", (svgY - 15).toString());
    foreignObj.setAttribute("width", "150");
    foreignObj.setAttribute("height", "30");
    foreignObj.setAttribute("overflow", "visible");

    const input = document.createElement("input");
    input.type = "text";
    input.classList.add("rm-name-input", `rm-name-input-${ch}`);
    input.value = names[ch];
    input.setAttribute("data-channel", ch);

    input.addEventListener("blur", () => {
      const newName = input.value.trim() || names[ch];
      if (newName !== names[ch]) {
        names[ch] = newName;
        nameChangeCallbacks.forEach((cb) => cb(ch, newName));
      }
      input.value = names[ch];
    });

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        input.blur();
      }
    });

    foreignObj.appendChild(input);
    inputsGroup.appendChild(foreignObj);
    return input;
  };

  // "click to pump x mL" hint drawn inside the clickable cloud / funnel rim,
  // so clicking the text itself also doses
  const doseHints = {} as Record<Channel, SVGTextElement>;
  const createDoseHint = (ch: Channel, parent: SVGGElement, x: number, y: number) => {
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", String(x));
    t.setAttribute("y", String(y));
    t.setAttribute("text-anchor", "middle");
    t.classList.add("rm-dose-hint");
    parent.appendChild(t);
    doseHints[ch] = t;
  };
  const renderDoseHint = (ch: Channel) => {
    doseHints[ch].textContent = `click to pump ${doseMl[ch]} mL`;
  };
  createDoseHint("A", cloudElements.A.group, 170, 160);
  createDoseHint("B", cloudElements.B.group, 450, 160);
  createDoseHint("C", cloudElements.C.group, 730, 160);
  createDoseHint("D", bowlGroup, 450, 318);
  for (const ch of ["A", "B", "C", "D"] as Channel[]) renderDoseHint(ch);

  // One small number field with a caption underneath, for the dose controls
  const createDoseField = (
    ch: Channel,
    opts: { title: string; unit: string; step: string; max?: string; get: () => number; set: (v: number) => void }
  ) => {
    const wrap = document.createElement("label");
    wrap.classList.add("rm-dose", `rm-dose-${ch}`);
    wrap.title = opts.title;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    if (opts.max) input.max = opts.max;
    input.step = opts.step;
    input.value = String(opts.get());
    input.classList.add("rm-dose-input");
    input.setAttribute("data-channel", ch);
    input.addEventListener("change", () => {
      // Invalid entries snap back to the last good value
      opts.set(parseFloat(input.value));
      input.value = String(opts.get());
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") input.blur();
    });
    const unit = document.createElement("span");
    unit.textContent = opts.unit;
    wrap.append(input, unit);
    return wrap;
  };

  // "mL per click" and "speed %" fields just left of each cloud and the funnel (svgX = their right edge)
  const createDoseInput = (ch: Channel, svgX: number, svgY: number) => {
    const foreignObj = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
    foreignObj.setAttribute("x", (svgX - 64).toString());
    foreignObj.setAttribute("y", (svgY - 44).toString());
    foreignObj.setAttribute("width", "64");
    foreignObj.setAttribute("height", "88");
    foreignObj.setAttribute("overflow", "visible");

    const stack = document.createElement("div");
    stack.classList.add("rm-dose-stack");
    stack.append(
      createDoseField(ch, {
        title: `mL pumped per click on ${ch}`,
        unit: "mL/click",
        step: "0.1",
        get: () => doseMl[ch],
        set: (v) => {
          if (v > 0) doseMl[ch] = v;
          renderDoseHint(ch);
        },
      }),
      createDoseField(ch, {
        title: `Pump speed for a click on ${ch} (% of max flow)`,
        unit: "speed %",
        step: "5",
        max: "100",
        get: () => doseSpeed[ch],
        set: (v) => {
          if (v > 0 && v <= 100) doseSpeed[ch] = v;
        },
      })
    );

    foreignObj.appendChild(stack);
    inputsGroup.appendChild(foreignObj);
  };

  // "pumped this session" total shown above each cloud and the funnel
  const totalEls = {} as Record<Channel, HTMLDivElement>;
  const createTotal = (ch: Channel, svgX: number, svgY: number) => {
    const foreignObj = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
    foreignObj.setAttribute("x", (svgX - 120).toString());
    foreignObj.setAttribute("y", (svgY - 13).toString());
    foreignObj.setAttribute("width", "240");
    foreignObj.setAttribute("height", "26");
    const el = document.createElement("div");
    el.classList.add("rm-total", `rm-total-${ch}`);
    el.title = `Total pumped on ${ch} this session`;
    foreignObj.appendChild(el);
    inputsGroup.appendChild(foreignObj);
    totalEls[ch] = el;
  };
  const renderTotal = (ch: Channel, ml: number) => {
    totalEls[ch].textContent = `💧 ${ml.toFixed(2)} mL pumped in total`;
  };
  createTotal("A", 170, 42);
  createTotal("B", 450, 42);
  createTotal("C", 730, 42);
  createTotal("D", 450, 268);
  for (const ch of ["A", "B", "C", "D"] as Channel[]) renderTotal(ch, 0);

  // Position inputs below clouds and on bowl
  createNameInput("A", 170, 200);
  createNameInput("B", 450, 200);
  createNameInput("C", 730, 200);
  createNameInput("D", 450, 410);
  createDoseInput("A", 170 - 100, 120);
  createDoseInput("B", 450 - 100, 120);
  createDoseInput("C", 730 - 100, 120);
  createDoseInput("D", 190 - 8, 360);

  // "rotate to" angle field right of the robot
  const angleObj = document.createElementNS("http://www.w3.org/2000/svg", "foreignObject");
  angleObj.setAttribute("x", "800");
  angleObj.setAttribute("y", "540");
  angleObj.setAttribute("width", "64");
  angleObj.setAttribute("height", "50");
  angleObj.setAttribute("overflow", "visible");
  const angleField = createDoseField("D", {
    title: `Angle a click on the robot rotates the cuvette to (0-${ROTATE_MAX_DEG}°)`,
    unit: "rotate to °",
    step: "5",
    max: String(ROTATE_MAX_DEG),
    get: () => rotateDeg,
    set: (v) => {
      if (Number.isInteger(v) && v >= 0 && v <= ROTATE_MAX_DEG) rotateDeg = v;
      renderRobot();
    },
  });
  angleField.classList.add("rm-angle");
  angleObj.appendChild(angleField);
  inputsGroup.appendChild(angleObj);

  // ---- Animation engine -------------------------------------------------
  // One requestAnimationFrame loop drives everything, using real frame time (dt),
  // so volumes are exact regardless of frame rate, and pausing simply skips updates.
  type Flow = { kind: "rain"; ch: SourceChannel; rate: number; remaining: number } | { kind: "pour"; rate: number; remaining: number };
  const flows: Flow[] = [];
  const cloudX: Record<SourceChannel, number> = { A: 170, B: 450, C: 730 };
  const CLOUD_BOTTOM = 170;
  const RIM_Y = 330;
  const SPOUT_Y = 520;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const DROPS_PER_SEC = reducedMotion ? 3 : 9;
  const MAX_DROPS = 60;

  const colours = () => ({
    A: getCSSVar("--col-a", "#A8E8F0"),
    B: getCSSVar("--col-b", "#FFB8D9"),
    C: getCSSVar("--col-c", "#FFE7A0"),
  });
  const inks: Record<SourceChannel, string> = {
    A: "var(--col-a-ink, #4FB3C6)",
    B: "var(--col-b-ink, #E07AAE)",
    C: "var(--col-c-ink, #D9B34A)",
  };

  const dropsLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
  dropsLayer.classList.add("rm-drops-layer");
  svg.insertBefore(dropsLayer, bowlGroup);

  const liquidSurface = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
  liquidSurface.classList.add("rm-liquid-surface");
  liquidGroup.appendChild(liquidSurface);

  interface Drop { el: SVGPathElement; x0: number; x1: number; y: number; y0: number; vy: number; }
  const drops: Drop[] = [];
  interface TubeDrop { el: SVGCircleElement; t: number; }
  const tubeDrops: TubeDrop[] = [];
  const spawnAcc: Record<Channel, number> = { A: 0, B: 0, C: 0, D: 0 };

  const levelY = () => {
    const total = bowlState.A + bowlState.B + bowlState.C;
    // cube root: a funnel's volume grows with height^3, so small volumes stay visible
    const f = Math.min(Math.cbrt(total / 50), 1);
    return SPOUT_Y - (SPOUT_Y - RIM_Y) * f;
  };

  const addDrop = (ch: SourceChannel) => {
    if (drops.length >= MAX_DROPS) return;
    const el = document.createElementNS("http://www.w3.org/2000/svg", "path");
    el.setAttribute("d", "M 0 -9 C 4 -3 5 1 0 4 C -5 1 -4 -3 0 -9 Z");
    el.setAttribute("fill", colours()[ch]);
    el.setAttribute("stroke", inks[ch]);
    el.setAttribute("stroke-width", "1.2");
    el.classList.add("rm-drop", `rm-drop-${ch}`);
    dropsLayer.appendChild(el);
    const x0 = cloudX[ch] + (Math.random() - 0.5) * 120;
    // steer drops so they land inside the funnel rim
    const x1 = 450 + (x0 - 450) * 0.55 + (Math.random() - 0.5) * 40;
    drops.push({ el, x0, x1, y: CLOUD_BOTTOM, y0: CLOUD_BOTTOM, vy: 0.32 + Math.random() * 0.08 });
  };

  const addTubeDrop = () => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    el.setAttribute("r", "4");
    el.setAttribute("fill", liquidColour());
    el.setAttribute("stroke", "var(--col-d-ink, #9A7FD6)");
    el.setAttribute("stroke-width", "1");
    tubeDropsGroup.appendChild(el);
    tubeDrops.push({ el, t: 0 });
  };

  const addRipple = (x: number, y: number) => {
    const r = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
    r.setAttribute("cx", String(x));
    r.setAttribute("cy", String(y));
    r.classList.add("rm-ripple");
    dropsLayer.appendChild(r);
    setTimeout(() => r.remove(), 600);
  };

  const liquidColour = () => {
    const c = colours();
    return blendColors(bowlState.A, bowlState.B, bowlState.C, c.A, c.B, c.C);
  };

  const updateRainingClasses = () => {
    for (const ch of ["A", "B", "C"] as SourceChannel[]) {
      const on = flows.some((f) => f.kind === "rain" && f.ch === ch);
      cloudElements[ch].group.classList.toggle("rm-raining", on);
    }
    const pouring = flows.some((f) => f.kind === "pour");
    tubeGroup.classList.toggle("rm-pouring", pouring);
  };

  let lastFrame = performance.now();
  const tick = (now: number) => {
    const dt = Math.min(now - lastFrame, 100);
    lastFrame = now;

    if (!paused) {
      // advance flows
      for (const f of flows) {
        const step = Math.min(dt, f.remaining);
        f.remaining -= step;
        if (f.kind === "rain") {
          bowlState[f.ch] += f.rate * step;
        } else {
          const total = bowlState.A + bowlState.B + bowlState.C;
          const out = Math.min(f.rate * step, total);
          if (total > 0) {
            for (const k of ["A", "B", "C"] as SourceChannel[]) bowlState[k] -= out * (bowlState[k] / total);
          }
        }
      }
      for (let i = flows.length - 1; i >= 0; i--) if (flows[i].remaining <= 0) flows.splice(i, 1);
      updateRainingClasses();

      // spawn drops for active clouds / tube
      for (const ch of ["A", "B", "C", "D"] as Channel[]) {
        const active =
          ch === "D"
            ? flows.some((f) => f.kind === "pour")
            : flows.some((f) => f.kind === "rain" && f.ch === ch);
        if (!active) { spawnAcc[ch] = 0; continue; }
        spawnAcc[ch] += (dt / 1000) * (ch === "D" ? 4 : DROPS_PER_SEC);
        while (spawnAcc[ch] >= 1) {
          spawnAcc[ch] -= 1;
          if (ch === "D") addTubeDrop(); else addDrop(ch);
        }
      }

      // move rain drops
      const surface = levelY();
      for (let i = drops.length - 1; i >= 0; i--) {
        const d = drops[i];
        d.vy += 0.0006 * dt;
        d.y += d.vy * dt;
        const p = Math.min((d.y - d.y0) / Math.max(surface - d.y0, 1), 1);
        const x = d.x0 + (d.x1 - d.x0) * p;
        if (d.y >= surface) {
          addRipple(x, surface);
          d.el.remove();
          drops.splice(i, 1);
        } else {
          d.el.setAttribute("transform", `translate(${x.toFixed(1)}, ${d.y.toFixed(1)})`);
        }
      }

      // move tube drops along the tube
      const len = tube.getTotalLength();
      for (let i = tubeDrops.length - 1; i >= 0; i--) {
        const td = tubeDrops[i];
        td.t += dt / 900;
        if (td.t >= 1) { td.el.remove(); tubeDrops.splice(i, 1); continue; }
        const pt = tube.getPointAtLength(td.t * len);
        td.el.setAttribute("cx", pt.x.toFixed(1));
        td.el.setAttribute("cy", pt.y.toFixed(1));
      }

      updateBowlVisual(now);

      // ease the mug's tilt towards the rotator's angle at roughly the motor's speed
      if (cupAngle !== cupTarget) {
        const maxStep = reducedMotion ? Infinity : dt / 1000 / rotate.sPerDeg;
        const diff = cupTarget - cupAngle;
        cupAngle = Math.abs(diff) <= maxStep ? cupTarget : cupAngle + Math.sign(diff) * maxStep;
        cupTilt.setAttribute("transform", `rotate(${cupAngle.toFixed(2)} 605 580)`);
        if (rotatorState === "moving") renderRobot();
      }
    }
    requestAnimationFrame(tick);
  };

  // A click on a cloud or the bowl asks for one manual dose on that channel
  const handleDoseClick = (ch: Channel, e: MouseEvent) => {
    if ((e.target as HTMLElement).closest(".rm-name-input")) return; // Don't trigger if clicking on input
    doseCallbacks.forEach((cb) => cb(ch, doseMl[ch], doseSpeed[ch]));
  };

  for (const ch of ["A", "B", "C"] as SourceChannel[]) {
    const cloudGroup = cloudElements[ch].group;
    cloudGroup.classList.add("rm-interactive");
    cloudGroup.addEventListener("click", (e) => handleDoseClick(ch, e));
  }

  bowlGroup.classList.add("rm-interactive");
  bowlGroup.addEventListener("click", (e) => handleDoseClick("D", e));

  // Append SVG to the stage wrapper
  stage.appendChild(svg);

  // Append stage to container
  container.appendChild(stage);

  // Draw the liquid inside the straight-sided funnel (rim 190..710 at y=330, tip at 450,520)
  const updateBowlVisual = (now: number = performance.now()) => {
    const total = bowlState.A + bowlState.B + bowlState.C;
    const colour = liquidColour();
    if (total <= 0.001) {
      liquidPath.setAttribute("d", "");
      liquidSurface.setAttribute("rx", "0");
      liquidSurface.setAttribute("ry", "0");
      return;
    }
    const y = levelY();
    const hw = 260 * ((SPOUT_Y - y) / (SPOUT_Y - RIM_Y));
    const ry = Math.max(45 * (hw / 260), 2);
    const wobble = reducedMotion ? 0 : Math.sin(now / 350) * 1.5;
    liquidPath.setAttribute(
      "d",
      `M ${450 - hw} ${y} A ${hw} ${ry} 0 0 0 ${450 + hw} ${y} L 450 ${SPOUT_Y} Z`
    );
    liquidPath.setAttribute("fill", colour);
    liquidSurface.setAttribute("cx", "450");
    liquidSurface.setAttribute("cy", (y + wobble).toFixed(1));
    liquidSurface.setAttribute("rx", hw.toFixed(1));
    liquidSurface.setAttribute("ry", ry.toFixed(1));
    liquidSurface.setAttribute("fill", colour);
    const cup = cupTilt.querySelector("path");
    if (cup) cup.setAttribute("fill", tubeDrops.length ? colour : "var(--col-d-empty, #EDE7FA)");
  };

  updateBowlVisual();
  requestAnimationFrame(tick);

  return {
    setNames(newNames: Record<Channel, string>) {
      Object.assign(names, newNames);
      for (const [ch, name] of Object.entries(names)) {
        const input = inputsGroup.querySelector(`.rm-name-input-${ch}`) as HTMLInputElement;
        if (input) input.value = name;
      }
    },

    onNameChange(cb: (ch: Channel, name: string) => void) {
      nameChangeCallbacks.push(cb);
    },

    startRain(ch: SourceChannel, ml: number, s: number) {
      if (s <= 0) {
        bowlState[ch] += Math.max(ml, 0);
        updateBowlVisual();
        return;
      }
      flows.push({ kind: "rain", ch, rate: Math.max(ml, 0) / (s * 1000), remaining: s * 1000 });
      updateRainingClasses();
    },

    startPour(ml: number, s: number) {
      if (s <= 0) {
        const total = bowlState.A + bowlState.B + bowlState.C;
        const out = Math.min(Math.max(ml, 0), total);
        if (total > 0) for (const k of ["A", "B", "C"] as SourceChannel[]) bowlState[k] -= out * (bowlState[k] / total);
        updateBowlVisual();
        return;
      }
      flows.push({ kind: "pour", rate: Math.max(ml, 0) / (s * 1000), remaining: s * 1000 });
      updateRainingClasses();
    },

    setPaused(p: boolean) {
      paused = p;
      svg.classList.toggle("rm-paused", p);
    },

    stopAll() {
      flows.length = 0;
      updateRainingClasses();
    },

    onDose(cb: (ch: Channel, ml: number, speed: number) => void) {
      doseCallbacks.push(cb);
    },

    setTotals(totals: Record<Channel, number>) {
      for (const ch of ["A", "B", "C", "D"] as Channel[]) renderTotal(ch, totals[ch]);
    },

    setRotator(state: RotatorState, deg: number | null, target?: number, message?: string) {
      rotatorState = state;
      rotatorDeg = deg;
      rotatorTarget = target ?? null;
      if (state === "moving" && target !== undefined) cupTarget = target;
      else if (state === "homing") cupTarget = 0;
      else if (deg !== null) cupTarget = deg;
      robotGroup.querySelector("title")?.remove();
      if (message) {
        const t = document.createElementNS("http://www.w3.org/2000/svg", "title");
        t.textContent = message;
        robotGroup.appendChild(t);
      }
      renderRobot();
    },

    onRotate(cb: (deg: number) => void) {
      rotateCallbacks.push(cb);
    },

    onRotatorConnect(cb: () => void) {
      connectCallbacks.push(cb);
    },

    resetBowl() {
      bowlState.A = 0;
      bowlState.B = 0;
      bowlState.C = 0;
      updateBowlVisual();
    },

    getBowl() {
      return {
        A: bowlState.A,
        B: bowlState.B,
        C: bowlState.C,
        total: bowlState.A + bowlState.B + bowlState.C,
      };
    },
  };
}
