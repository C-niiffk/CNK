package com.example.management.agent;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(AgentProperties.class)
@ComponentScan(basePackageClasses = GrdcClient.class)
public class AgentConfiguration {
}
