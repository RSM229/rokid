pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        maven { url = uri("https://maven.rokid.com/repository/maven-public/") }
        maven { url = uri("https://rokid-ota.oss-cn-hangzhou.aliyuncs.com/toB/maven") }
    }
}

rootProject.name = "GlassesApp"
include(":app", ":phone-companion")
