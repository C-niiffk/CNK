# Build context: management-platform/, not grdc/.
# Keep the upstream distribution and startup script, but explicitly use Java 21.
FROM nacos/nacos-server:v3.2.1 AS distribution

FROM maven:3.9.11-eclipse-temurin-21 AS jdbc
RUN mvn -B -ntp dependency:copy \
    -Dartifact=com.oracle.database.jdbc:ojdbc11:23.9.0.25.07 \
    -DoutputDirectory=/jdbc

FROM eclipse-temurin:21-jdk-jammy
ENV BASE_DIR=/home/nacos \
    CLUSTER_CONF=/home/nacos/conf/cluster.conf \
    JAVA=/opt/java/openjdk/bin/java \
    MODE=standalone \
    FUNCTION_MODE=microservice \
    PREFER_HOST_MODE=ip \
    JVM_XMS=256m \
    JVM_XMX=768m \
    JVM_XMN=128m \
    JVM_MS=128m \
    JVM_MMS=320m \
    NACOS_DEBUG=n
WORKDIR /home/nacos
COPY --from=distribution /home/nacos/ /home/nacos/
COPY --from=jdbc /jdbc/*.jar /home/nacos/plugins/
COPY grdc/application.properties /home/nacos/conf/application.properties
COPY grdc/LICENSE-NACOS.txt grdc/NOTICE-NACOS.txt /home/nacos/
RUN test -f /home/nacos/target/nacos-server.jar \
    && test -f /home/nacos/bin/docker-startup.sh \
    && mkdir -p /home/nacos/logs /home/nacos/data \
    && chown -R 10001:10001 /home/nacos \
    && java -version
USER 10001:10001
EXPOSE 8848 9848 8080
ENTRYPOINT ["bash", "/home/nacos/bin/docker-startup.sh"]
