using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

namespace AECStudio.Interop
{
    public sealed class UsageSnapshot
    {
        public bool Ok;
        public int ActiveSessions;
        public string Error;
        public int[] ProcessIds;
        public string[] Clients;
        internal long Updated;
    }

    public sealed class CaptureUsage : IDisposable
    {
        private readonly string expectedName;
        private readonly bool standardCable;
        private readonly ManualResetEvent stopped = new ManualResetEvent(false);
        private readonly object sessionsLock = new object();
        private readonly List<IAudioSessionControl> sessions = new List<IAudioSessionControl>();
        private readonly HashSet<long> identities = new HashSet<long>();
        private readonly Dictionary<int, string> processNames = new Dictionary<int, string>();
        private volatile UsageSnapshot snapshot;
        private readonly Thread thread;

        public CaptureUsage(string outputName)
        {
            int marker = outputName.IndexOf(" Input", StringComparison.OrdinalIgnoreCase);
            expectedName = marker < 0 ? "" : outputName.Substring(0, marker) + " Output" + outputName.Substring(marker + 6);
            standardCable = IsStandardCableName(outputName);
            Publish(false, 0, "starting");
            thread = new Thread(Watch);
            thread.IsBackground = true;
            thread.SetApartmentState(ApartmentState.MTA);
            thread.Start();
        }

        public static bool IsStandardCableName(string name)
        {
            return name.IndexOf("VB-Audio Virtual Cable", StringComparison.OrdinalIgnoreCase) >= 0 &&
                name.Replace(" ", "").IndexOf("16ch", StringComparison.OrdinalIgnoreCase) < 0;
        }

        public static bool CableEndpointsReady()
        {
            int initialized = CoInitializeEx(IntPtr.Zero, 0);
            IMMDeviceEnumerator enumerator = null;
            try
            {
                Check(initialized);
                enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
                for (int flow = 0; flow <= 1; flow++)
                {
                    IMMDeviceCollection devices = null;
                    bool found = false;
                    try
                    {
                        Check(enumerator.EnumAudioEndpoints(flow, 1, out devices));
                        uint count;
                        Check(devices.GetCount(out count));
                        for (uint index = 0; index < count; index++)
                        {
                            IMMDevice device = null;
                            try
                            {
                                Check(devices.Item(index, out device));
                                found |= IsStandardCableName(FriendlyName(device));
                            }
                            finally { Release(device); }
                        }
                    }
                    finally { Release(devices); }
                    if (!found) return false;
                }
                return true;
            }
            finally
            {
                Release(enumerator);
                if (initialized >= 0) CoUninitialize();
            }
        }

        public UsageSnapshot Read()
        {
            UsageSnapshot current = snapshot;
            if (DateTime.UtcNow.Ticks - current.Updated > TimeSpan.FromSeconds(5).Ticks)
                return new UsageSnapshot { Ok = false, Error = "usage_monitor_stale" };
            return current;
        }

        private void Publish(bool ok, int count, string error, int[] processIds = null)
        {
            List<string> clients = new List<string>();
            Dictionary<int, string> activeNames = new Dictionary<int, string>();
            if (processIds != null) foreach (int identifier in processIds)
            {
                string name;
                if (!processNames.TryGetValue(identifier, out name))
                    try { using (System.Diagnostics.Process process = System.Diagnostics.Process.GetProcessById(identifier)) name = process.ProcessName; }
                    catch { name = "PID " + identifier.ToString(); }
                activeNames[identifier] = name;
                clients.Add(name);
            }
            processNames.Clear();
            foreach (KeyValuePair<int, string> entry in activeNames) processNames.Add(entry.Key, entry.Value);
            snapshot = new UsageSnapshot { Ok = ok, ActiveSessions = count, Error = error, ProcessIds = processIds, Clients = clients.ToArray(), Updated = DateTime.UtcNow.Ticks };
        }

        private void Add(IAudioSessionControl control)
        {
            IntPtr identity = Marshal.GetIUnknownForObject(control);
            long key = identity.ToInt64();
            Marshal.Release(identity);
            lock (sessionsLock)
            {
                if (identities.Add(key)) sessions.Add(control);
                else Release(control);
            }
        }

        private int CountActive(out int[] processIds)
        {
            int count = 0;
            HashSet<int> active = new HashSet<int>();
            lock (sessionsLock)
                for (int index = sessions.Count - 1; index >= 0; index--)
                {
                    IAudioSessionControl control = sessions[index];
                    int state;
                    Check(control.GetState(out state));
                    if (state == 1)
                    {
                        count++;
                        try { uint identifier; Check(((IAudioSessionControl2)control).GetProcessId(out identifier)); active.Add((int)identifier); } catch { }
                    }
                    else if (state == 2)
                    {
                        IntPtr identity = Marshal.GetIUnknownForObject(control);
                        identities.Remove(identity.ToInt64());
                        Marshal.Release(identity);
                        sessions.RemoveAt(index);
                        Release(control);
                    }
                }
            processIds = new List<int>(active).ToArray();
            return count;
        }

        private void ClearSessions()
        {
            lock (sessionsLock)
            {
                foreach (IAudioSessionControl control in sessions) Release(control);
                sessions.Clear();
                identities.Clear();
            }
        }

        private void Watch()
        {
            int initialized = CoInitializeEx(IntPtr.Zero, 0);
            try
            {
                Check(initialized);
                while (!stopped.WaitOne(0))
                {
                    IMMDeviceEnumerator enumerator = null;
                    IMMDeviceCollection devices = null;
                    IMMDevice selected = null;
                    IAudioSessionManager2 manager = null;
                    IAudioSessionEnumerator sessionEnumerator = null;
                    SessionNotification notification = null;
                    bool registered = false;
                    try
                    {
                        if (expectedName.Length == 0 && !standardCable) throw new InvalidOperationException("unsupported_virtual_output");
                        enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
                        Check(enumerator.EnumAudioEndpoints(1, 1, out devices));
                        uint count;
                        Check(devices.GetCount(out count));
                        for (uint index = 0; index < count; index++)
                        {
                            IMMDevice device;
                            Check(devices.Item(index, out device));
                            string name = FriendlyName(device);
                            bool matches = String.Equals(name, expectedName, StringComparison.OrdinalIgnoreCase) ||
                                (standardCable && IsStandardCableName(name));
                            if (matches)
                            {
                                if (selected != null) { Release(device); throw new InvalidOperationException("ambiguous_virtual_input"); }
                                selected = device;
                            }
                            else Release(device);
                        }
                        if (selected == null) throw new InvalidOperationException("virtual_input_not_found");
                        Guid identifier = typeof(IAudioSessionManager2).GUID;
                        object activated;
                        Check(selected.Activate(ref identifier, 23, IntPtr.Zero, out activated));
                        manager = (IAudioSessionManager2)activated;
                        notification = new SessionNotification(Add);
                        Check(manager.RegisterSessionNotification(notification));
                        registered = true;
                        Check(manager.GetSessionEnumerator(out sessionEnumerator));
                        int sessionCount;
                        Check(sessionEnumerator.GetCount(out sessionCount));
                        for (int index = 0; index < sessionCount; index++)
                        {
                            IAudioSessionControl control;
                            Check(sessionEnumerator.GetSession(index, out control));
                            Add(control);
                        }
                        while (!stopped.WaitOne(0))
                        {
                            int[] processIds;
                            int active = CountActive(out processIds);
                            Publish(true, active, "", processIds);
                            if (stopped.WaitOne(500)) break;
                        }
                    }
                    catch (Exception error) { Publish(false, 0, error.GetType().Name + ": " + error.Message); }
                    finally
                    {
                        if (registered) try { manager.UnregisterSessionNotification(notification); } catch { }
                        ClearSessions();
                        Release(sessionEnumerator); Release(manager); Release(selected); Release(devices); Release(enumerator);
                    }
                    if (stopped.WaitOne(2000)) break;
                }
            }
            catch (Exception error) { Publish(false, 0, error.Message); }
            finally { if (initialized >= 0) CoUninitialize(); }
        }

        public void Dispose() { stopped.Set(); thread.Join(300); }
        private static void Check(int result) { if (result < 0) Marshal.ThrowExceptionForHR(result); }
        private static void Release(object value) { if (value != null && Marshal.IsComObject(value)) Marshal.ReleaseComObject(value); }

        private static string FriendlyName(IMMDevice device)
        {
            IPropertyStore store;
            Check(device.OpenPropertyStore(0, out store));
            PropVariant value = new PropVariant();
            try
            {
                PropertyKey key = new PropertyKey { Format = new Guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"), Id = 14 };
                Check(store.GetValue(ref key, out value));
                return value.Type == 31 ? Marshal.PtrToStringUni(value.Pointer) : "";
            }
            finally { PropVariantClear(ref value); Release(store); }
        }

        [DllImport("ole32.dll")] private static extern int CoInitializeEx(IntPtr reserved, uint mode);
        [DllImport("ole32.dll")] private static extern void CoUninitialize();
        [DllImport("ole32.dll")] private static extern int PropVariantClear(ref PropVariant value);
        [StructLayout(LayoutKind.Sequential)] private struct PropertyKey { public Guid Format; public uint Id; }
        [StructLayout(LayoutKind.Explicit, Size = 24)] private struct PropVariant { [FieldOffset(0)] public ushort Type; [FieldOffset(8)] public IntPtr Pointer; }

        [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] private class MMDeviceEnumerator { }
        [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IMMDeviceEnumerator { [PreserveSig] int EnumAudioEndpoints(int flow, uint state, out IMMDeviceCollection collection); }
        [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IMMDeviceCollection { [PreserveSig] int GetCount(out uint count); [PreserveSig] int Item(uint index, out IMMDevice device); }
        [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IMMDevice
        {
            [PreserveSig] int Activate(ref Guid identifier, uint context, IntPtr activation, [MarshalAs(UnmanagedType.IUnknown)] out object instance);
            [PreserveSig] int OpenPropertyStore(uint access, out IPropertyStore properties);
        }
        [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IPropertyStore
        {
            [PreserveSig] int GetCount(out uint count);
            [PreserveSig] int GetAt(uint index, out PropertyKey key);
            [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
        }
        [ComImport, Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IAudioSessionManager2
        {
            [PreserveSig] int GetAudioSessionControl(IntPtr identifier, uint flags, out IntPtr control);
            [PreserveSig] int GetSimpleAudioVolume(IntPtr identifier, uint flags, out IntPtr volume);
            [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator enumerator);
            [PreserveSig] int RegisterSessionNotification(IAudioSessionNotification notification);
            [PreserveSig] int UnregisterSessionNotification(IAudioSessionNotification notification);
        }
        [ComImport, Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IAudioSessionEnumerator
        {
            [PreserveSig] int GetCount(out int count);
            [PreserveSig] int GetSession(int index, out IAudioSessionControl control);
        }
    }

    [ComImport, Guid("F4B1A599-7266-4319-A8CA-E70ACB11E8CD"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAudioSessionControl { [PreserveSig] int GetState(out int state); }
    [ComImport, Guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAudioSessionControl2
    {
        [PreserveSig] int GetState(out int state);
        [PreserveSig] int GetDisplayName(out IntPtr name);
        [PreserveSig] int SetDisplayName(IntPtr name, IntPtr context);
        [PreserveSig] int GetIconPath(out IntPtr path);
        [PreserveSig] int SetIconPath(IntPtr path, IntPtr context);
        [PreserveSig] int GetGroupingParam(out Guid grouping);
        [PreserveSig] int SetGroupingParam(ref Guid grouping, IntPtr context);
        [PreserveSig] int RegisterAudioSessionNotification(IntPtr notification);
        [PreserveSig] int UnregisterAudioSessionNotification(IntPtr notification);
        [PreserveSig] int GetSessionIdentifier(out IntPtr identifier);
        [PreserveSig] int GetSessionInstanceIdentifier(out IntPtr identifier);
        [PreserveSig] int GetProcessId(out uint identifier);
    }
    [ComVisible(true), Guid("641DD20B-4D41-49CC-ABA3-174B9477BB08"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAudioSessionNotification { [PreserveSig] int OnSessionCreated(IAudioSessionControl session); }
    [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
    public sealed class SessionNotification : IAudioSessionNotification
    {
        private readonly Action<IAudioSessionControl> added;
        public SessionNotification(Action<IAudioSessionControl> callback) { added = callback; }
        public int OnSessionCreated(IAudioSessionControl session) { try { added(session); return 0; } catch { return unchecked((int)0x80004005); } }
    }
}
