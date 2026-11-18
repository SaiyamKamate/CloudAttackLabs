# Build the static dashboard, then serve it with nginx.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Serves the SPA and proxies /api to the backend (see nginx.conf).
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 5173
