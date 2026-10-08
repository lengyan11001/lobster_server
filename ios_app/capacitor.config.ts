import type { CapacitorConfig } from "@capacitor/cli";

const configuredUrl = (
    process.env.LOBSTER_APP_URL || process.env.OPENMIND_APP_URL
)?.trim();

const config: CapacitorConfig = {
    appId: "com.bihuo.lobsterios",
    appName: "必火AI员工",
    webDir: "h5/dist",
    loggingBehavior: "production",
};

if (configuredUrl) {
    let parsedUrl: URL;

    try {
        parsedUrl = new URL(configuredUrl);
    } catch {
        throw new Error(`LOBSTER_APP_URL is not a valid URL: ${configuredUrl}`);
    }

    config.server = {
        url: parsedUrl.toString(),
        cleartext: parsedUrl.protocol === "http:",
        allowNavigation: [parsedUrl.hostname],
    };
}

export default config;
