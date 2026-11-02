using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Threading;

internal static class DriverHelper
{
    private const string HardwareId = "VBAudioVACWDM";
    private static Guid MediaClass = new Guid("4d36e96c-e325-11ce-bfc1-08002be10318");
    private static Guid EndpointClass = new Guid("c166523c-fe0c-4a94-a586-f1a80cfbbf3e");
    private static readonly IntPtr InvalidHandle = new IntPtr(-1);
    private static bool restartPending;

    private static void Log(string message)
    {
        try
        {
            string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "AECStudio");
            Directory.CreateDirectory(folder);
            File.AppendAllText(Path.Combine(folder, "driver-install.log"), DateTime.UtcNow.ToString("O") + " " + message + Environment.NewLine);
        }
        catch { }
    }

    [DllImport("cfgmgr32.dll")]
    private static extern uint CM_Get_DevNode_Status(out uint status, out uint problem, uint instance, uint flags);

    [StructLayout(LayoutKind.Sequential)]
    private struct DeviceInfo
    {
        internal uint Size;
        internal Guid ClassGuid;
        internal uint DeviceInstance;
        internal UIntPtr Reserved;
    }

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr SetupDiGetClassDevsW(ref Guid classGuid, IntPtr enumerator, IntPtr parent, uint flags);
    [DllImport("setupapi.dll", SetLastError = true)]
    private static extern bool SetupDiEnumDeviceInfo(IntPtr devices, uint index, ref DeviceInfo info);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetupDiGetDeviceRegistryPropertyW(IntPtr devices, ref DeviceInfo info, uint property, out uint type, byte[] buffer, uint size, out uint required);
    [DllImport("setupapi.dll", SetLastError = true)]
    private static extern bool SetupDiDestroyDeviceInfoList(IntPtr devices);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetupDiGetINFClassW(string inf, out Guid classGuid, StringBuilder className, uint size, out uint required);
    [DllImport("setupapi.dll", SetLastError = true)]
    private static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid classGuid, IntPtr parent);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetupDiCreateDeviceInfoW(IntPtr devices, string name, ref Guid classGuid, string description, IntPtr parent, uint flags, ref DeviceInfo info);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetupDiSetDeviceRegistryPropertyW(IntPtr devices, ref DeviceInfo info, uint property, byte[] buffer, uint size);
    [DllImport("setupapi.dll", SetLastError = true)]
    private static extern bool SetupDiCallClassInstaller(uint function, IntPtr devices, ref DeviceInfo info);
    [DllImport("newdev.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool UpdateDriverForPlugAndPlayDevicesW(IntPtr parent, string hardwareId, string inf, uint flags, out bool reboot);

    private static string Property(IntPtr devices, ref DeviceInfo info, uint property)
    {
        byte[] buffer = new byte[16384];
        uint type, required;
        if (!SetupDiGetDeviceRegistryPropertyW(devices, ref info, property, out type, buffer, (uint)buffer.Length, out required)) return "";
        return Encoding.Unicode.GetString(buffer, 0, (int)Math.Min(required, (uint)buffer.Length)).TrimEnd('\0');
    }

    private static bool DriverPresent()
    {
        IntPtr devices = SetupDiGetClassDevsW(ref MediaClass, IntPtr.Zero, IntPtr.Zero, 2);
        if (devices == InvalidHandle) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            DeviceInfo info = new DeviceInfo();
            info.Size = (uint)Marshal.SizeOf(typeof(DeviceInfo));
            for (uint index = 0; SetupDiEnumDeviceInfo(devices, index, ref info); index++)
            {
                foreach (string hardware in Property(devices, ref info, 1).Split('\0'))
                    if (String.Equals(hardware, HardwareId, StringComparison.OrdinalIgnoreCase))
                    {
                        uint status, problem;
                        uint result = CM_Get_DevNode_Status(out status, out problem, info.DeviceInstance, 0);
                        restartPending = result == 0 && (status & 0x400) != 0 && problem == 14;
                        Log("Driver present; statusResult=" + result + "; status=" + status + "; problem=" + problem);
                        return true;
                    }
            }
            return false;
        }
        finally { SetupDiDestroyDeviceInfoList(devices); }
    }

    private static bool EndpointsReady()
    {
        IntPtr devices = SetupDiGetClassDevsW(ref EndpointClass, IntPtr.Zero, IntPtr.Zero, 2);
        if (devices == InvalidHandle) return false;
        bool input = false, output = false;
        try
        {
            DeviceInfo info = new DeviceInfo();
            info.Size = (uint)Marshal.SizeOf(typeof(DeviceInfo));
            for (uint index = 0; SetupDiEnumDeviceInfo(devices, index, ref info); index++)
            {
                string name = Property(devices, ref info, 12);
                if (name.Length == 0) name = Property(devices, ref info, 0);
                input |= name.IndexOf("CABLE Input", StringComparison.OrdinalIgnoreCase) >= 0;
                output |= name.IndexOf("CABLE Output", StringComparison.OrdinalIgnoreCase) >= 0;
            }
            return input && output;
        }
        finally { SetupDiDestroyDeviceInfoList(devices); }
    }

    private static bool WaitForEndpoints()
    {
        for (int attempt = 0; attempt < 120; attempt++)
        {
            if (EndpointsReady()) return true;
            Thread.Sleep(500);
        }
        return false;
    }

    private static int Install(string filename)
    {
        if (!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator)) return 5;
        if (DriverPresent()) return restartPending ? 3010 : WaitForEndpoints() ? 0 : 3;
        string inf = Path.GetFullPath(filename);
        if (!File.Exists(inf) || !String.Equals(Path.GetFileName(inf), "vbMmeCable64_win10.inf", StringComparison.OrdinalIgnoreCase)) return 2;
        Guid classGuid;
        uint required;
        StringBuilder className = new StringBuilder(256);
        if (!SetupDiGetINFClassW(inf, out classGuid, className, 256, out required)) throw new Win32Exception(Marshal.GetLastWin32Error());
        if (classGuid != MediaClass) throw new InvalidOperationException("Unexpected driver class.");
        IntPtr devices = SetupDiCreateDeviceInfoList(ref classGuid, IntPtr.Zero);
        if (devices == InvalidHandle) throw new Win32Exception(Marshal.GetLastWin32Error());
        DeviceInfo info = new DeviceInfo();
        info.Size = (uint)Marshal.SizeOf(typeof(DeviceInfo));
        bool registered = false;
        bool installed = false;
        try
        {
            if (!SetupDiCreateDeviceInfoW(devices, className.ToString(), ref classGuid, "VB-Audio Virtual Cable", IntPtr.Zero, 1, ref info)) throw new Win32Exception(Marshal.GetLastWin32Error());
            byte[] hardware = Encoding.Unicode.GetBytes(HardwareId + "\0\0");
            if (!SetupDiSetDeviceRegistryPropertyW(devices, ref info, 1, hardware, (uint)hardware.Length)) throw new Win32Exception(Marshal.GetLastWin32Error());
            if (!SetupDiCallClassInstaller(0x19, devices, ref info)) throw new Win32Exception(Marshal.GetLastWin32Error());
            registered = true;
            bool reboot;
            if (!UpdateDriverForPlugAndPlayDevicesW(IntPtr.Zero, HardwareId, inf, 1, out reboot)) throw new Win32Exception(Marshal.GetLastWin32Error());
            installed = true;
            if (reboot) return 3010;
            if (WaitForEndpoints()) return 0;
            DriverPresent();
            return restartPending ? 3010 : 3;
        }
        finally
        {
            if (registered && !installed) SetupDiCallClassInstaller(5, devices, ref info);
            SetupDiDestroyDeviceInfoList(devices);
        }
    }

    private static int Run(string[] args)
    {
        try
        {
            if (args.Length == 1 && args[0] == "--check")
            {
                if (!DriverPresent()) return 2;
                return restartPending ? 3010 : EndpointsReady() ? 0 : 3;
            }
            if (args.Length == 2 && args[0] == "--install") return Install(args[1]);
            return 64;
        }
        catch (Exception error)
        {
            try
            {
                string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "AECStudio");
                Directory.CreateDirectory(folder);
                File.AppendAllText(Path.Combine(folder, "driver-install.log"), DateTime.UtcNow.ToString("O") + " " + error + Environment.NewLine);
            }
            catch { }
            return 1;
        }
    }

    private static int Main(string[] args)
    {
        int result = Run(args);
        Log(String.Join(" ", args) + "; result=" + result);
        return result;
    }
}
