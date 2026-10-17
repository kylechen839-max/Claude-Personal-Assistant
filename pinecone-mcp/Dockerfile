FROM node:22-slim
WORKDIR /app
COPY package*.json tsconfig.json ./
RUN npm ci
COPY src ./src
RUN npm run build && npm prune --omit=dev
ENV PORT=3000
EXPOSE 3000
CMD ["node", "dist/server.js"]
