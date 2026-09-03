# ============ 前端镜像：多阶段（node 构建 → nginx 静态服务） ============
# nginx 反代配置由 compose 挂载 deploy/nginx.conf 覆盖默认配置。
FROM node:20-alpine AS builder
WORKDIR /web
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /web/dist /srv/web
