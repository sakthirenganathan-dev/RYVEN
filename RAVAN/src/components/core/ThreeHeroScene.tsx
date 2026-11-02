import { useEffect, useRef, useState, useMemo } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { CoreState } from "@/types/jarvis";

interface HeroSceneProps {
  state: CoreState;
  level: number;
  onClick?: (() => void) | undefined;
}

// -------------------------------------------------------------
// 1. Shaders
// -------------------------------------------------------------

const plasmaVertexShader = `
varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vPosition;

void main() {
  vUv = uv;
  vNormal = normalize(normalMatrix * normal);
  vPosition = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const plasmaFragmentShader = `
uniform float uTime;
uniform vec3 uColor1;
uniform vec3 uColor2;
uniform vec3 uColor3;
uniform float uAudio;

varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vPosition;

float hash(vec3 p) {
  p = fract(p * 0.3183099 + .1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}

float noise(in vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(hash(i + vec3(0,0,0)), hash(i + vec3(1,0,0)), f.x),
        mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
    mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x),
        mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z
  );
}

float fbm(vec3 p) {
  float f = 0.0;
  f += 0.5000 * noise(p); p *= 2.02;
  f += 0.2500 * noise(p); p *= 2.03;
  f += 0.1250 * noise(p); p *= 2.01;
  return f;
}

void main() {
  vec3 p = vPosition * 3.2 + vec3(uTime * 0.14, uTime * 0.10, uTime * 0.08);
  float n1 = fbm(p);
  float n2 = fbm(p + vec3(n1 * 1.5, n1 * 1.2, uTime * 0.18));
  
  // Vibrant multi-layer swirling plasma colors
  vec3 color = mix(uColor3, uColor2, smoothstep(0.12, 0.65, n2));
  color = mix(color, uColor1, smoothstep(0.38, 0.85, n1 + uAudio * 0.35));
  
  // Inner core energy luminance
  float core = pow(n2, 2.2) * 1.6;
  color += uColor1 * core * 0.85;
  
  // Edge glow
  float fresnel = pow(1.0 - abs(dot(vNormal, vec3(0.0, 0.0, 1.0))), 2.2);
  color += uColor1 * fresnel * 0.75;
  
  gl_FragColor = vec4(color, 0.96);
}
`;

// Crystal-clear Glass Fresnel Shader
const glassVertexShader = `
varying vec3 vNormal;
varying vec3 vViewPosition;
varying vec2 vUv;

void main() {
  vUv = uv;
  vNormal = normalize(normalMatrix * normal);
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  vViewPosition = -mvPosition.xyz;
  gl_Position = projectionMatrix * mvPosition;
}
`;

const glassFragmentShader = `
uniform vec3 uColor;
uniform vec3 uRimColor;

varying vec3 vNormal;
varying vec3 vViewPosition;
varying vec2 vUv;

void main() {
  vec3 viewDir = normalize(vViewPosition);
  float fresnel = pow(1.0 - max(dot(viewDir, vNormal), 0.0), 2.8);
  
  // Specular highlight glint on top-left
  vec3 lightDir = normalize(vec3(-0.9, 1.2, 1.1));
  vec3 halfDir = normalize(lightDir + viewDir);
  float spec = pow(max(dot(vNormal, halfDir), 0.0), 38.0);
  
  vec3 col = mix(uColor, uRimColor, fresnel * 0.9);
  col += vec3(1.0, 1.0, 1.0) * spec * 0.95;
  
  float alpha = fresnel * 0.65 + spec * 0.85 + 0.04;
  gl_FragColor = vec4(col, clamp(alpha, 0.0, 0.95));
}
`;

const haloVertexShader = `
varying vec3 vNormal;

void main() {
  vNormal = normalize(normalMatrix * normal);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const haloFragmentShader = `
uniform vec3 uColor;
uniform float uIntensity;

varying vec3 vNormal;

void main() {
  float intensity = pow(0.72 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 2.6);
  gl_FragColor = vec4(uColor, clamp(intensity * uIntensity, 0.0, 1.0));
}
`;

const beamVertexShader = `
varying vec2 vUv;

void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const beamFragmentShader = `
uniform float uTime;
uniform vec3 uColor;
uniform float uPulse;

varying vec2 vUv;

void main() {
  float distFromCenter = abs(vUv.x - 0.5) * 2.0;
  float coreBeam = exp(-distFromCenter * distFromCenter * 20.0);
  float glowBeam = exp(-distFromCenter * distFromCenter * 3.5) * 0.45;
  
  float vertFade = smoothstep(0.04, 0.35, vUv.y) * smoothstep(0.96, 0.65, vUv.y);
  float wave = sin(vUv.y * 16.0 - uTime * 3.5) * 0.08 + 0.92;
  
  float alpha = (coreBeam + glowBeam) * vertFade * wave * uPulse;
  vec3 col = mix(uColor, vec3(1.0, 1.0, 1.0), coreBeam * 0.8);
  
  gl_FragColor = vec4(col, alpha);
}
`;

// Helper: Procedural high-res Delta logo texture
function createRyvenLogoTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext("2d")!;
  ctx.clearRect(0, 0, 512, 512);

  // Outer cyan glow
  ctx.shadowColor = "rgba(0, 240, 255, 1.0)";
  ctx.shadowBlur = 32;
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#22d3ee";
  ctx.lineWidth = 12;

  // Draw Ryven Delta glyph
  ctx.beginPath();
  ctx.moveTo(256, 85);
  ctx.lineTo(95, 415);
  ctx.lineTo(178, 415);
  ctx.lineTo(256, 235);
  ctx.lineTo(334, 415);
  ctx.lineTo(417, 415);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

// -------------------------------------------------------------
// 2. Central AI Core Orb Component
// -------------------------------------------------------------

function AIOrbCore({
  state,
  level,
}: {
  state: CoreState;
  level: number;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const plasmaMeshRef = useRef<THREE.Mesh>(null);
  const plasmaMatRef = useRef<THREE.ShaderMaterial>(null);
  const glassMatRef = useRef<THREE.ShaderMaterial>(null);
  const haloMatRef = useRef<THREE.ShaderMaterial>(null);
  const logoMeshRef = useRef<THREE.Mesh>(null);

  const logoTexture = useMemo(() => {
    if (typeof window === "undefined") return null;
    return createRyvenLogoTexture();
  }, []);

  const targetColors = useMemo(() => {
    switch (state) {
      case "listening":
        return {
          c1: new THREE.Color("#22c55e"),
          c2: new THREE.Color("#06b6d4"),
          c3: new THREE.Color("#0f172a"),
          speed: 1.6,
        };
      case "thinking":
        return {
          c1: new THREE.Color("#a855f7"),
          c2: new THREE.Color("#ec4899"),
          c3: new THREE.Color("#1e1b4b"),
          speed: 2.4,
        };
      case "speaking":
        return {
          c1: new THREE.Color("#67e8f9"),
          c2: new THREE.Color("#38bdf8"),
          c3: new THREE.Color("#1e3a8a"),
          speed: 1.8,
        };
      case "error":
        return {
          c1: new THREE.Color("#ef4444"),
          c2: new THREE.Color("#b91c1c"),
          c3: new THREE.Color("#450a0a"),
          speed: 0.8,
        };
      default: // idle
        return {
          c1: new THREE.Color("#22d3ee"),
          c2: new THREE.Color("#8b5cf6"),
          c3: new THREE.Color("#030712"),
          speed: 1.0,
        };
    }
  }, [state]);

  const plasmaUniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uColor1: { value: targetColors.c1 },
      uColor2: { value: targetColors.c2 },
      uColor3: { value: targetColors.c3 },
      uAudio: { value: 0 },
    }),
    []
  );

  const glassUniforms = useMemo(
    () => ({
      uColor: { value: new THREE.Color("#22d3ee") },
      uRimColor: { value: new THREE.Color("#8b5cf6") },
    }),
    []
  );

  const haloUniforms = useMemo(
    () => ({
      uColor: { value: targetColors.c1 },
      uIntensity: { value: 0.8 },
    }),
    []
  );

  useFrame((stateThree, delta) => {
    const t = stateThree.clock.getElapsedTime();

    // Subtle floating levitation for the entire core
    if (groupRef.current) {
      groupRef.current.position.y = Math.sin(t * 1.5) * 0.05;
      groupRef.current.rotation.y = Math.sin(t * 0.5) * 0.06;
    }

    // Inner plasma sphere spins continuously
    if (plasmaMeshRef.current) {
      plasmaMeshRef.current.rotation.y = t * 0.18;
      plasmaMeshRef.current.rotation.x = Math.sin(t * 0.4) * 0.08;
    }

    // Logo breathing pulse
    if (logoMeshRef.current) {
      const pulse = 1.0 + Math.sin(t * 3.0) * 0.05 + level * 0.18;
      logoMeshRef.current.scale.set(pulse, pulse, pulse);
    }

    // Update plasma shader
    if (plasmaMatRef.current) {
      const u = plasmaMatRef.current.uniforms;
      if (u["uTime"]) u["uTime"].value = t * targetColors.speed;
      if (u["uAudio"]) u["uAudio"].value = level;
      if (u["uColor1"]) (u["uColor1"].value as THREE.Color).lerp(targetColors.c1, delta * 3.5);
      if (u["uColor2"]) (u["uColor2"].value as THREE.Color).lerp(targetColors.c2, delta * 3.5);
      if (u["uColor3"]) (u["uColor3"].value as THREE.Color).lerp(targetColors.c3, delta * 3.5);
    }

    // Update glass rim color
    if (glassMatRef.current) {
      const u = glassMatRef.current.uniforms;
      if (u["uColor"]) (u["uColor"].value as THREE.Color).lerp(targetColors.c1, delta * 3.5);
    }

    // Update halo
    if (haloMatRef.current) {
      const u = haloMatRef.current.uniforms;
      if (u["uColor"]) (u["uColor"].value as THREE.Color).lerp(targetColors.c1, delta * 3.5);
      if (u["uIntensity"]) u["uIntensity"].value = 0.8 + level * 0.35;
    }
  });

  return (
    <group ref={groupRef}>
      {/* 1. Inner Swirling Galaxy/Plasma Sphere */}
      <mesh ref={plasmaMeshRef}>
        <sphereGeometry args={[0.82, 64, 64]} />
        <shaderMaterial
          ref={plasmaMatRef}
          vertexShader={plasmaVertexShader}
          fragmentShader={plasmaFragmentShader}
          uniforms={plasmaUniforms}
          transparent
        />
      </mesh>

      {/* 2. Floating Glowing Ryven "A" Delta Logo in Front */}
      {logoTexture && (
        <mesh ref={logoMeshRef} position={[0, 0, 0.52]} renderOrder={10}>
          <planeGeometry args={[0.72, 0.72]} />
          <meshBasicMaterial
            map={logoTexture}
            transparent
            blending={THREE.AdditiveBlending}
            depthTest={false}
            depthWrite={false}
          />
        </mesh>
      )}

      {/* 3. Outer Crystal Glass Sphere with Fresnel and Specular Reflection */}
      <mesh>
        <sphereGeometry args={[0.95, 64, 64]} />
        <shaderMaterial
          ref={glassMatRef}
          vertexShader={glassVertexShader}
          fragmentShader={glassFragmentShader}
          uniforms={glassUniforms}
          transparent
          blending={THREE.NormalBlending}
          depthWrite={false}
        />
      </mesh>

      {/* 4. Outer Soft Atmospheric Halo Sphere */}
      <mesh>
        <sphereGeometry args={[1.08, 48, 48]} />
        <shaderMaterial
          ref={haloMatRef}
          vertexShader={haloVertexShader}
          fragmentShader={haloFragmentShader}
          uniforms={haloUniforms}
          transparent
          blending={THREE.AdditiveBlending}
          side={THREE.BackSide}
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}

// -------------------------------------------------------------
// 3. Orbital Rings + Orbiting Planets Component
// -------------------------------------------------------------

function OrbitalSystem({
  state,
}: {
  state: CoreState;
}) {
  const ring1Ref = useRef<THREE.Group>(null);
  const ring2Ref = useRef<THREE.Group>(null);
  const ring3Ref = useRef<THREE.Group>(null);

  const speedMult = state === "thinking" ? 2.5 : state === "listening" ? 1.4 : 1.0;

  const ringGeo1 = useMemo(() => new THREE.TorusGeometry(1.5, 0.008, 16, 128), []);
  const ringGeo2 = useMemo(() => new THREE.TorusGeometry(1.85, 0.008, 16, 128), []);
  const ringGeo3 = useMemo(() => new THREE.TorusGeometry(2.2, 0.008, 16, 128), []);

  useFrame((_, delta) => {
    if (ring1Ref.current) ring1Ref.current.rotation.z += delta * 0.45 * speedMult;
    if (ring2Ref.current) ring2Ref.current.rotation.z -= delta * 0.32 * speedMult;
    if (ring3Ref.current) ring3Ref.current.rotation.z += delta * 0.22 * speedMult;
  });

  return (
    <>
      {/* Ring 1: Cyan Tilted Orbit */}
      <group rotation={[Math.PI * 0.35, Math.PI * 0.12, -Math.PI * 0.1]}>
        <mesh geometry={ringGeo1}>
          <meshBasicMaterial
            color="#22d3ee"
            transparent
            opacity={0.75}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
        <group ref={ring1Ref}>
          {/* Planet 1: Violet/Pink */}
          <mesh position={[1.5, 0, 0]}>
            <sphereGeometry args={[0.055, 20, 20]} />
            <meshStandardMaterial
              color="#ec4899"
              emissive="#ec4899"
              emissiveIntensity={3.0}
            />
            <pointLight color="#ec4899" intensity={1.5} distance={2.0} />
          </mesh>
        </group>
      </group>

      {/* Ring 2: Violet Opposing Orbit */}
      <group rotation={[-Math.PI * 0.28, -Math.PI * 0.18, Math.PI * 0.22]}>
        <mesh geometry={ringGeo2}>
          <meshBasicMaterial
            color="#a855f7"
            transparent
            opacity={0.7}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
        <group ref={ring2Ref}>
          {/* Planet 2: Cyan/White */}
          <mesh position={[1.85, 0, 0]}>
            <sphereGeometry args={[0.065, 20, 20]} />
            <meshStandardMaterial
              color="#38bdf8"
              emissive="#38bdf8"
              emissiveIntensity={3.2}
            />
            <pointLight color="#38bdf8" intensity={1.8} distance={2.5} />
          </mesh>
        </group>
      </group>

      {/* Ring 3: Electric Blue Outer Orbit */}
      <group rotation={[Math.PI * 0.15, Math.PI * 0.32, -Math.PI * 0.25]}>
        <mesh geometry={ringGeo3}>
          <meshBasicMaterial
            color="#3b82f6"
            transparent
            opacity={0.55}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
        <group ref={ring3Ref}>
          {/* Planet 3: Violet Core */}
          <mesh position={[2.2, 0, 0]}>
            <sphereGeometry args={[0.05, 20, 20]} />
            <meshStandardMaterial
              color="#8b5cf6"
              emissive="#8b5cf6"
              emissiveIntensity={2.5}
            />
            <pointLight color="#8b5cf6" intensity={1.2} distance={2.0} />
          </mesh>
        </group>
      </group>
    </>
  );
}

// -------------------------------------------------------------
// 4. Volumetric Vertical Light Beam & Rising Sparks
// -------------------------------------------------------------

function VolumetricLightBeam({
  state,
}: {
  state: CoreState;
}) {
  const beamMatRef = useRef<THREE.ShaderMaterial>(null);

  const beamColor = useMemo(() => {
    if (state === "thinking") return new THREE.Color("#c084fc");
    if (state === "listening") return new THREE.Color("#4ade80");
    if (state === "error") return new THREE.Color("#ef4444");
    return new THREE.Color("#22d3ee");
  }, [state]);

  const beamUniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uColor: { value: beamColor },
      uPulse: { value: 1.0 },
    }),
    []
  );

  useFrame((stateThree, delta) => {
    const t = stateThree.clock.getElapsedTime();
    if (beamMatRef.current) {
      const u = beamMatRef.current.uniforms;
      if (u["uTime"]) u["uTime"].value = t;
      if (u["uPulse"]) u["uPulse"].value = 0.85 + Math.sin(t * 2.0) * 0.18;
      if (u["uColor"]) (u["uColor"].value as THREE.Color).lerp(beamColor, delta * 3.5);
    }
  });

  return (
    <group position={[0, 0, 0]}>
      {/* Volumetric Soft Outer Beam */}
      <mesh>
        <cylinderGeometry args={[0.16, 0.28, 9.0, 32, 1, true]} />
        <shaderMaterial
          ref={beamMatRef}
          vertexShader={beamVertexShader}
          fragmentShader={beamFragmentShader}
          uniforms={beamUniforms}
          transparent
          blending={THREE.AdditiveBlending}
          side={THREE.DoubleSide}
          depthWrite={false}
        />
      </mesh>

      {/* Laser Bright Inner Core */}
      <mesh>
        <cylinderGeometry args={[0.04, 0.05, 9.0, 16, 1, true]} />
        <meshBasicMaterial
          color="#ffffff"
          transparent
          opacity={0.85}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}

// -------------------------------------------------------------
// 5. Holographic Floor Platform & Reflector Disc Component
// -------------------------------------------------------------

function FloorPlatform() {
  const ring1Ref = useRef<THREE.Group>(null);
  const ring2Ref = useRef<THREE.Group>(null);

  const platformGeo1 = useMemo(() => new THREE.RingGeometry(1.2, 1.22, 64), []);
  const platformGeo2 = useMemo(() => new THREE.RingGeometry(1.65, 1.68, 64), []);
  const platformGeo3 = useMemo(() => new THREE.RingGeometry(2.1, 2.13, 64), []);

  useFrame((_, delta) => {
    if (ring1Ref.current) ring1Ref.current.rotation.z += delta * 0.15;
    if (ring2Ref.current) ring2Ref.current.rotation.z -= delta * 0.22;
  });

  return (
    <group position={[0, -1.65, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      {/* Soft Center Floor Light-Pool */}
      <mesh position={[0, 0, -0.02]}>
        <circleGeometry args={[2.8, 64]} />
        <meshBasicMaterial
          color="#06b6d4"
          transparent
          opacity={0.16}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>

      {/* Ring 1 - Inner Smooth Concentric Ring */}
      <mesh geometry={platformGeo1}>
        <meshBasicMaterial
          color="#22d3ee"
          transparent
          opacity={0.85}
          blending={THREE.AdditiveBlending}
        />
      </mesh>

      {/* Ring 2 - Rotating Segmented Middle Ring */}
      <group ref={ring1Ref}>
        <mesh geometry={platformGeo2}>
          <meshBasicMaterial
            color="#8b5cf6"
            transparent
            opacity={0.7}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
      </group>

      {/* Ring 3 - Counter-Rotating Outer Perimeter */}
      <group ref={ring2Ref}>
        <mesh geometry={platformGeo3}>
          <meshBasicMaterial
            color="#22d3ee"
            transparent
            opacity={0.55}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
      </group>

      {/* Glossy Floor Disc (Simulated Reflection of Orb & Beam) */}
      <mesh position={[0, 0, -0.05]}>
        <circleGeometry args={[1.5, 32]} />
        <meshBasicMaterial
          color="#22d3ee"
          transparent
          opacity={0.25}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}

// -------------------------------------------------------------
// 6. Master ThreeHeroScene Export
// -------------------------------------------------------------

export function ThreeHeroScene({ state, level, onClick }: HeroSceneProps) {
  const [hasWebGL, setHasWebGL] = useState(true);

  useEffect(() => {
    try {
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
      if (!gl) setHasWebGL(false);
    } catch {
      setHasWebGL(false);
    }
  }, []);

  if (!hasWebGL) {
    return null;
  }

  return (
    <div
      onClick={onClick}
      role={onClick ? "button" : undefined}
      className="relative h-[430px] w-full max-w-[560px] cursor-pointer flex items-center justify-center select-none"
    >
      <Canvas
        camera={{ position: [0, 0.2, 5.2], fov: 42 }}
        dpr={Math.min(1.75, typeof window !== "undefined" ? window.devicePixelRatio : 1)}
        gl={{
          antialias: true,
          alpha: true,
          powerPreference: "high-performance",
          toneMapping: THREE.ACESFilmicToneMapping,
          toneMappingExposure: 1.15,
        }}
      >
        <ambientLight intensity={0.5} />

        {/* 1. Volumetric Vertical Light Beam */}
        <VolumetricLightBeam state={state} />

        {/* 2. Central AI Core Orb */}
        <AIOrbCore state={state} level={level} />

        {/* 3. 3 Tilted Orbital Rings + Orbiting Planets */}
        <OrbitalSystem state={state} />

        {/* 4. Concentric Floor Platform Rings */}
        <FloorPlatform />

      </Canvas>
    </div>
  );
}
