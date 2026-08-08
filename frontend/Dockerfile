# Stage 1: build the React app
FROM node:20-alpine AS build
WORKDIR /app

# npmmirror keeps builds fast on JD Cloud (China mainland)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com

COPY frontend/ ./
RUN npm run build

# Stage 2: serve with nginx
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80 443
