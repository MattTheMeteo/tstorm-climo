/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // React integrations ship ESM that Next needs to transform. MapLibre 6
  // bundles normally; keeping it out avoids Turbopack treating its runtime
  // worker-URL import fallback as a build-time module dependency.
  transpilePackages: [
    "react-map-gl",
    "@deck.gl/react",
    "@deck.gl/layers",
    "@deck.gl/core",
  ],
};

export default nextConfig;
