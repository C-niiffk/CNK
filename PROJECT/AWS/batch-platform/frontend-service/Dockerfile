FROM maven:3.9.11-eclipse-temurin-21 AS build
WORKDIR /build

COPY pom.xml ./pom.xml

COPY frontend-service/pom.xml frontend-service/pom.xml
COPY frontend-service/src frontend-service/src

RUN mvn -B -ntp -f frontend-service/pom.xml verify

FROM eclipse-temurin:21-jre-jammy
RUN apt-get update && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 batch \
    && useradd -u 10001 -g batch -m batch \
    && mkdir -p /app /work \
    && chown 10001:10001 /work \
    && chmod 1777 /tmp

WORKDIR /app
COPY --from=build /build/frontend-service/target/frontend-service.jar /app/service.jar

VOLUME ["/tmp"]
USER 10001:10001

ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=65 -XX:+ExitOnOutOfMemoryError -Duser.timezone=UTC"

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "java", "-jar", "/app/service.jar"]
