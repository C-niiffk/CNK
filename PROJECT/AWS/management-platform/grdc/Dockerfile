FROM maven:3.9.11-eclipse-temurin-21 AS jdbc
RUN mvn -B -ntp dependency:copy -Dartifact=com.oracle.database.jdbc:ojdbc11:23.9.0.25.07 -DoutputDirectory=/jdbc
FROM nacos/nacos-server:v3.2.1
COPY --from=jdbc /jdbc/*.jar /home/nacos/plugins/
