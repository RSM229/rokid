# Rokid SDKs use Binder callbacks, service discovery and reflection internally.
-keep class com.rokid.** { *; }
-keep interface com.rokid.** { *; }
-keepattributes Signature,InnerClasses,EnclosingMethod,*Annotation*
-dontwarn java.lang.invoke.StringConcatFactory
-dontwarn android.app.ActivityThread
-dontwarn android.app.AppGlobals
-dontwarn android.os.SystemProperties
-dontwarn android.util.Pools$SimplePool
-dontwarn kotlinx.parcelize.Parcelize

# Room entities and Gson network models.
-keep class com.gdjl.glasses.data.local.entities.** { *; }
-keep class com.gdjl.glasses.network.models.** { *; }
-dontwarn org.slf4j.**
