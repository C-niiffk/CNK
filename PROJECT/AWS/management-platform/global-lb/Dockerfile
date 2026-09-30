FROM apache/skywalking-java-agent:9.6.0-java21 AS agent
FROM maven:3.9.11-eclipse-temurin-21 AS build
WORKDIR /src
COPY . .
ARG MODULE=global-lb
RUN test -n "$MODULE" && mvn -B -ntp -pl "$MODULE" -am package -DskipTests && cp "$MODULE"/target/*-1.0.0.jar /app.jar
FROM eclipse-temurin:21-jre
RUN mkdir -p /logs /data /opt/agent && chown -R 10001:0 /logs /data /opt/agent
COPY --from=build /app.jar /app.jar
COPY --from=agent /skywalking/agent /opt/agent
USER 10001
EXPOSE 8080
ENTRYPOINT ["java","-jar","/app.jar"]
