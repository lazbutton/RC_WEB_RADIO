FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY dist/ /usr/share/nginx/html/
COPY brand.json /usr/share/nginx/html/brand.json
EXPOSE 80
