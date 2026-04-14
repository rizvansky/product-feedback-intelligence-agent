const internalApiBaseUrl = (
  process.env.PFIA_INTERNAL_API_BASE_URL || "http://127.0.0.1:8000"
).replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  experimental: {
    typedRoutes: false
  },
  async rewrites() {
    return [
      {
        source: "/pfia/:path*",
        destination: `${internalApiBaseUrl}/:path*`
      }
    ];
  }
};

export default nextConfig;
