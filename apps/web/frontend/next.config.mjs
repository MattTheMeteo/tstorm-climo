/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The map libraries ship untranspiled ESM; let Next transform them.
  transpilePackages: [
    "maplibre-gl",
    "react-map-gl",
    "@deck.gl/react",
    "@deck.gl/layers",
    "@deck.gl/core",
  ],
};

export default nextConfig;
