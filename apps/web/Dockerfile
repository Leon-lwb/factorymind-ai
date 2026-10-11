# FactoryMind AI web — container image.
#
# Build context is the repository root (the pnpm workspace must be visible):
#   docker build -f apps/web/Dockerfile -t factorymind-ai-web .
#
# Stage 1 builds the SPA; stage 2 serves it and proxies `/api` to the engine,
# so the browser talks to one origin — the same shape `vite dev` gives locally.
FROM node:22-alpine AS build

# Dependencies come from a mirror by default: from the target network the public
# registry measured ~280 kB/s versus ~4.3 MB/s here — a 15x difference that
# decides whether the build is minutes or hours. `COREPACK_NPM_REGISTRY` covers
# the pnpm download itself, which corepack fetches before any install runs.
# Override for other environments:
#   docker build --build-arg NPM_REGISTRY=https://registry.npmjs.org ...
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV COREPACK_NPM_REGISTRY=${NPM_REGISTRY} \
    npm_config_registry=${NPM_REGISTRY}

RUN corepack enable

WORKDIR /repo

# Manifests first: `pnpm install` then caches until a dependency actually changes.
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY packages/shared-types/package.json packages/shared-types/
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile

# `@factorymind/shared-types` is consumed straight from source (`main: src/index.ts`),
# so no package build step is needed before the app build.
COPY packages/shared-types packages/shared-types
COPY apps/web apps/web
RUN pnpm --filter @factorymind/web build

FROM nginx:1.27-alpine

COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/web/dist /usr/share/nginx/html

EXPOSE 80